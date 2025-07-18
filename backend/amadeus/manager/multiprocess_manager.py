import asyncio
import multiprocessing
import re
import sys
import traceback
from enum import Enum, unique
from queue import Empty
from typing import Any, Callable, Dict, Optional, Set, Union

from loguru import logger

from amadeus.common import blue, green, red, yellow


@unique
class MultiprocessState(Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class MultiprocessManager:
    _managers: Dict[str, 'MultiprocessManager'] = {}
    _mp_context = multiprocessing.get_context("spawn")

    def __new__(cls, name: str, *args, **kwargs):
        if name in cls._managers:
            return cls._managers[name]
        instance = super().__new__(cls)
        cls._managers[name] = instance
        return instance

    def __init__(self,
                 name: str,
                 target: Callable[..., Any],
                 args: tuple = (),
                 kwargs: Optional[dict] = None,
                 custom_states: Optional[Dict[str, str]] = None,
                 stream_logs: bool = False):
        if hasattr(self, '_initialized'):
            return

        self.name = name
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.stream_logs = stream_logs
        
        self.custom_state_patterns: Dict[str, re.Pattern] = {}
        if custom_states:
            for state_name, pattern in custom_states.items():
                self.custom_state_patterns[state_name] = re.compile(pattern)

        self._current_state: Union[MultiprocessState, str] = MultiprocessState.PENDING
        self._state_changed_event = asyncio.Event()

        self._process: Optional[multiprocessing.Process] = None
        self._log_queue: Optional[multiprocessing.Queue] = None
        self._monitor_tasks: Set[asyncio.Task] = set()
        self.metadata: Dict[str, Any] = {}  # For storing additional metadata
        self._initialized = True

    @property
    def current_state(self) -> Union[MultiprocessState, str]:
        return self._current_state

    async def set_state(self, new_state: Union[MultiprocessState, str]):
        if self._current_state != new_state:
            logger.info(f"Subprocess {blue(self.name)} state changed to: {green(new_state.value if isinstance(new_state, Enum) else new_state)}")
            self._current_state = new_state
            self._state_changed_event.set()
            self._state_changed_event.clear()

    @staticmethod
    def _process_wrapper(log_queue: multiprocessing.Queue, target: Callable[..., Any], *args, **kwargs):
        class QueueWriter:
            def __init__(self, queue: multiprocessing.Queue):
                self.queue = queue
            def write(self, message: str):
                if message.strip():
                    self.queue.put(message)
            def flush(self):
                pass
        
        sys.stdout = QueueWriter(log_queue)
        sys.stderr = QueueWriter(log_queue)

        try:
            target(*args, **kwargs)
        except Exception:
            # Log the exception to the queue
            logger.error(f"An exception occurred in the child process:\n{traceback.format_exc()}")
            # Re-raise the exception to ensure the process exits with error code
            raise


    def _start_monitoring_tasks(self):
        if not self._monitor_tasks:
            task1 = asyncio.create_task(self._monitor_process_state())
            task2 = asyncio.create_task(self._monitor_logs_for_custom_states())
            self._monitor_tasks.add(task1)
            self._monitor_tasks.add(task2)
            task1.add_done_callback(self._monitor_tasks.discard)
            task2.add_done_callback(self._monitor_tasks.discard)

    async def start(self):
        if self.current_state not in [MultiprocessState.PENDING, MultiprocessState.STOPPED, MultiprocessState.ERROR]:
            return

        await self.set_state(MultiprocessState.STARTING)
        
        self._log_queue = self._mp_context.Queue()
        
        process_args = (self._log_queue, self.target) + self.args
        self._process = self._mp_context.Process(target=self._process_wrapper, args=process_args, kwargs=self.kwargs)
        logger.info(f"Starting subprocess: {blue(self.name)} with target {green(self.target.__name__)}")
        self._process.start()
        
        await self.set_state(MultiprocessState.RUNNING)
        self._start_monitoring_tasks()

    async def stop(self):
        if self.current_state in [MultiprocessState.STOPPED, MultiprocessState.STOPPING]:
            return

        await self.set_state(MultiprocessState.STOPPING)
        logger.info(f"Stopping subprocess: {blue(self.name)}...")

        loop = asyncio.get_event_loop()
        if self._process and self._process.is_alive():
            self._process.terminate()
            try:
                await asyncio.wait_for(loop.run_in_executor(None, self._process.join), timeout=10)
            except asyncio.TimeoutError:
                if self._process.is_alive():
                    self._process.kill()
                    logger.warning(f"Subprocess {blue(self.name)} did not terminate gracefully, killing it.")
                    await loop.run_in_executor(None, self._process.join)
        
        await self.set_state(MultiprocessState.STOPPED)

        for task in self._monitor_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._monitor_tasks, return_exceptions=True)
        self._monitor_tasks.clear()


    async def _monitor_process_state(self):
        if not self._process:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._process.join)
        
        exitcode = self._process.exitcode
        
        if self._current_state not in [MultiprocessState.STOPPING, MultiprocessState.STOPPED]:
            if exitcode != 0:
                # Drain remaining logs from queue when process terminates unexpectedly
                remaining_logs = await self._drain_log_queue()
                
                logger.error(f"Subprocess {blue(self.name)} terminated unexpectedly with exit code {red(str(exitcode))}.")
                if remaining_logs:
                    logger.error(f"Remaining output from {blue(self.name)}:\n{remaining_logs}")
                
                await self.set_state(MultiprocessState.ERROR)
            else:
                logger.info(f"Subprocess {blue(self.name)} finished gracefully.")
                await self.set_state(MultiprocessState.STOPPED)
        else:
             await self.set_state(MultiprocessState.STOPPED)

    async def _drain_log_queue(self) -> str:
        """Drain remaining logs from the queue when process terminates unexpectedly."""
        if not self._log_queue:
            return ""
        
        remaining_logs = []
        loop = asyncio.get_event_loop()
        
        try:
            # Drain all remaining logs from the queue
            while True:
                try:
                    line = await asyncio.wait_for(
                        loop.run_in_executor(None, self._log_queue.get_nowait),
                        timeout=0.1
                    )
                    if line and line.strip():
                        remaining_logs.append(line.strip())
                except (Empty, asyncio.TimeoutError):
                    break
        except Exception as e:
            logger.debug(f"Error draining log queue: {e}")
        
        return "\n".join(remaining_logs)


    async def _monitor_logs_for_custom_states(self):
        if not self.custom_state_patterns and not self.stream_logs:
            return

        if not self._log_queue:
            return

        await self.wait_for_state(MultiprocessState.RUNNING)
        
        loop = asyncio.get_event_loop()
        
        while self.current_state == MultiprocessState.RUNNING or self.current_state in self.custom_state_patterns:
            try:
                line = await loop.run_in_executor(None, self._log_queue.get, True, 1.0)
                if not line:
                    continue
                
                line = line.strip()
                if self.stream_logs:
                    logger.opt(raw=True).info(f"[{blue(self.name)}] {line}\n")

                for state, pattern in self.custom_state_patterns.items():
                    if pattern.search(line):
                        await self.set_state(state)
                        
            except Empty:
                if self.current_state not in [MultiprocessState.RUNNING] and self.current_state not in self.custom_state_patterns:
                    break
                continue
            except Exception as e:
                logger.error(f"Error in log monitoring for subprocess {blue(self.name)}: {e}")
                break

    async def wait_for_state(self, state: Union[MultiprocessState, str], timeout: Optional[float] = None) -> bool:
        if self._current_state == state:
            return True

        async def _wait_loop():
            while self._current_state != state:
                await self._state_changed_event.wait()

        try:
            await asyncio.wait_for(_wait_loop(), timeout=timeout)
            return True
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False

    async def close(self):
        if self.current_state not in [MultiprocessState.STOPPED, MultiprocessState.STOPPING]:
            await self.stop()

        if self.name in MultiprocessManager._managers:
            del MultiprocessManager._managers[self.name]

    def __del__(self):
        if self.name in MultiprocessManager._managers:
            del MultiprocessManager._managers[self.name]

        for task in self._monitor_tasks:
            if not task.done():
                task.cancel() 