import asyncio
import re
from enum import Enum, unique
from typing import Dict, List, Optional, Set, Union

from loguru import logger


@unique
class SubprocessState(Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class SubprocessManager:
    _managers: Dict[str, 'SubprocessManager'] = {}

    def __new__(cls, name: str, *args, **kwargs):
        if name in cls._managers:
            return cls._managers[name]
        instance = super().__new__(cls)
        cls._managers[name] = instance
        return instance

    def __init__(self,
                 name: str,
                 run_command: List[str],
                 custom_states: Optional[Dict[str, str]] = None,
                 **kwargs):
        if hasattr(self, '_initialized'):
            return

        self.name = name
        self.run_command = run_command
        self.custom_state_patterns: Dict[str, re.Pattern] = {}
        if custom_states:
            for state_name, pattern in custom_states.items():
                self.custom_state_patterns[state_name] = re.compile(pattern)

        self._current_state: Union[SubprocessState, str] = SubprocessState.PENDING
        self._state_changed_event = asyncio.Event()

        self._process: Optional[asyncio.subprocess.Process] = None
        self._monitor_tasks: Set[asyncio.Task] = set()
        self._initialized = True

    @property
    def current_state(self) -> Union[SubprocessState, str]:
        return self._current_state

    async def set_state(self, new_state: Union[SubprocessState, str]):
        if self._current_state != new_state:
            logger.info(f"Process '{self.name}' state change: {self._current_state} -> {new_state}")
            self._current_state = new_state
            self._state_changed_event.set()
            self._state_changed_event.clear()

    def _start_monitoring_tasks(self):
        if not self._monitor_tasks:
            task1 = asyncio.create_task(self._monitor_process_state())
            task2 = asyncio.create_task(self._monitor_logs_for_custom_states())
            self._monitor_tasks.add(task1)
            self._monitor_tasks.add(task2)
            task1.add_done_callback(self._monitor_tasks.discard)
            task2.add_done_callback(self._monitor_tasks.discard)

    async def start(self):
        if self.current_state not in [SubprocessState.PENDING, SubprocessState.STOPPED, SubprocessState.ERROR]:
            return

        await self.set_state(SubprocessState.STARTING)

        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.run_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT 
            )
            await self.set_state(SubprocessState.RUNNING)
            self._start_monitoring_tasks()
        except (FileNotFoundError, Exception) as e:
            logger.error(f"Failed to start process '{self.name}': {e}")
            await self.set_state(SubprocessState.ERROR)

    async def stop(self):
        if self.current_state in [SubprocessState.STOPPED, SubprocessState.STOPPING]:
            return

        await self.set_state(SubprocessState.STOPPING)

        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        
        await self.set_state(SubprocessState.STOPPED)

        for task in self._monitor_tasks:
            if not task.done():
                task.cancel()
        self._monitor_tasks.clear()


    async def _monitor_process_state(self):
        if not self._process:
            return

        return_code = await self._process.wait()
        logger.info(f"Process '{self.name}' exited with code {return_code}.")

        current_state_before_wait = self.current_state

        if current_state_before_wait not in [SubprocessState.STOPPING, SubprocessState.STOPPED]:
            if return_code != 0:
                await self.set_state(SubprocessState.ERROR)
            else:
                await self.set_state(SubprocessState.STOPPED)
        else:
             await self.set_state(SubprocessState.STOPPED)


    async def _monitor_logs_for_custom_states(self):
        if not self.custom_state_patterns or not self._process or not self._process.stdout:
            return

        await self.wait_for_state(SubprocessState.RUNNING)

        while self.current_state == SubprocessState.RUNNING or self.current_state in self.custom_state_patterns:
            try:
                line_bytes = await asyncio.wait_for(self._process.stdout.readline(), timeout=1.0)
                if not line_bytes:
                    break

                line = line_bytes.decode('utf-8', errors='ignore').strip()
                for state, pattern in self.custom_state_patterns.items():
                    if pattern.search(line):
                        await self.set_state(state)

            except asyncio.TimeoutError:
                if self.current_state not in [SubprocessState.RUNNING] and self.current_state not in self.custom_state_patterns:
                    break
                continue
            except Exception:
                break

    async def wait_for_state(self, state: Union[SubprocessState, str], timeout: Optional[float] = None) -> bool:
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
        logger.info(f"Closing SubprocessManager for '{self.name}'")
        if self.current_state not in [SubprocessState.STOPPED, SubprocessState.STOPPING]:
            await self.stop()

        for task in self._monitor_tasks:
             if not task.done():
                task.cancel()
        
        await asyncio.gather(*self._monitor_tasks, return_exceptions=True)
        self._monitor_tasks.clear()

        if self.name in SubprocessManager._managers:
            del SubprocessManager._managers[self.name]

    def __del__(self):
        if self.name in SubprocessManager._managers:
            del SubprocessManager._managers[self.name]

        for task in self._monitor_tasks:
             if not task.done():
                task.cancel() 