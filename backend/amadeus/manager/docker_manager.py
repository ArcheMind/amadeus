import asyncio
import re
import sys
from enum import Enum, unique
from typing import Dict, List, Optional, Set, Union
from loguru import logger


@unique
class DockerContainerState(Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    REMOVING = "removing"
    ERROR = "error"


class DockerRunManager:
    _managers: Dict[str, 'DockerRunManager'] = {}

    def __new__(cls, container_name: str, *args, **kwargs):
        if container_name in cls._managers:
            return cls._managers[container_name]
        instance = super().__new__(cls)
        cls._managers[container_name] = instance
        return instance

    def __init__(self,
                 container_name: str,
                 image_name: str,
                 run_command: Optional[List[str]] = None,
                 custom_states: Optional[Dict[str, str]] = None,
                 **kwargs):
        if hasattr(self, '_initialized'):
            return
        
        self.container_name = container_name
        self.image_name = image_name
        self.run_command = run_command or []
        self.docker_run_options = kwargs

        self._current_state: Union[DockerContainerState, str] = DockerContainerState.PENDING
        self._state_changed_event = asyncio.Event()
        
        self.custom_state_patterns: Dict[str, re.Pattern] = {}
        if custom_states:
            for name, pattern in custom_states.items():
                self.custom_state_patterns[name] = re.compile(pattern)

        self._monitor_tasks: Set[asyncio.Task] = set()
        self._initialized = True
        
        loop = asyncio.get_event_loop()
        loop.create_task(self._initial_check())

    async def _initial_check(self):
        container_id = await self._get_container_id_by_name()
        if container_id:
            self._start_monitoring_tasks()

    @property
    def current_state(self) -> Union[DockerContainerState, str]:
        return self._current_state

    async def set_state(self, new_state: Union[DockerContainerState, str]):
        if self._current_state != new_state:
            logger.info(f"stage change: {self._current_state} -> {new_state}")
            self._current_state = new_state
            self._state_changed_event.set()
            self._state_changed_event.clear()

    def _build_docker_run_command(self) -> List[str]:
        cmd = ["docker", "run", "--name", self.container_name, "-d"]
        
        for key, value in self.docker_run_options.items():
            option = f"--{key.replace('_', '-')}"
            if isinstance(value, dict):
                for k, v in value.items():
                    if key == "ports":
                        cmd.extend(["-p", f"{k}:{v}"])
                    elif key == "volumes":
                        cmd.extend(["-v", f"{k}:{v}"])
                    elif key == "env":
                        cmd.extend(["-e", f"{k}={v}"])
            elif isinstance(value, bool) and value:
                cmd.append(option)
            elif isinstance(value, str):
                cmd.extend([option, value])
            elif isinstance(value, list):
                 for item in value:
                     cmd.extend([option, item])

        cmd.append(self.image_name)
        cmd.extend(self.run_command)
        return cmd

    async def _run_subprocess(self, *args) -> Optional[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                return None
            return stdout.decode().strip()
        except (FileNotFoundError, Exception):
            return None


    async def _get_container_id_by_name(self) -> Optional[str]:
        return await self._run_subprocess("docker", "ps", "-a", "--filter", f"name=^{self.container_name}$", "--format", "{{.ID}}")

    async def _get_container_state_by_id(self, container_id: str) -> Optional[str]:
         return await self._run_subprocess("docker", "inspect", "-f", "{{.State.Status}}", container_id)

    def _start_monitoring_tasks(self):
        if not self._monitor_tasks:
            task1 = asyncio.create_task(self._monitor_docker_state())
            task2 = asyncio.create_task(self._monitor_logs_for_custom_states())
            self._monitor_tasks.add(task1)
            self._monitor_tasks.add(task2)
            task1.add_done_callback(self._monitor_tasks.discard)
            task2.add_done_callback(self._monitor_tasks.discard)

    async def start(self):
        if self.current_state not in [DockerContainerState.PENDING, DockerContainerState.STOPPED, DockerContainerState.ERROR]:
            return

        await self.set_state(DockerContainerState.STARTING)
        container_id = await self._get_container_id_by_name()

        if not container_id:
            run_cmd = self._build_docker_run_command()
            new_container_id = await self._run_subprocess(*run_cmd)

            if not new_container_id:
                await self.set_state(DockerContainerState.ERROR)
                return
        else:
            await self._run_subprocess("docker", "start", container_id)

        self._start_monitoring_tasks()

    async def stop(self):
        if self.current_state in [DockerContainerState.STOPPED, DockerContainerState.STOPPING]:
             return

        await self.set_state(DockerContainerState.STOPPING)
        container_id = await self._get_container_id_by_name()
        if container_id:
            await self._run_subprocess("docker", "stop", container_id)
        
        await self.wait_for_state(DockerContainerState.STOPPED, timeout=30)
        
        for task in self._monitor_tasks:
            task.cancel()
        self._monitor_tasks.clear()

    async def remove(self):
        await self.set_state(DockerContainerState.REMOVING)
        container_id = await self._get_container_id_by_name()
        if container_id:
            await self._run_subprocess("docker", "rm", container_id)
            await self.set_state(DockerContainerState.STOPPED)
        else:
            await self.set_state(DockerContainerState.STOPPED)


    async def _monitor_docker_state(self):
        while self.current_state not in [DockerContainerState.STOPPED, DockerContainerState.ERROR]:
            container_id = await self._get_container_id_by_name()
            if not container_id:
                await self.set_state(DockerContainerState.STOPPED)
                break
            
            status = await self._get_container_state_by_id(container_id)
            
            new_state = self.current_state
            
            if status == "running":
                if self.current_state in [DockerContainerState.STARTING, DockerContainerState.PENDING]:
                    new_state = DockerContainerState.RUNNING
            elif status in ["exited", "dead", "removing"]:
                new_state = DockerContainerState.STOPPED
            elif status == "created":
                new_state = DockerContainerState.STARTING
            
            if self.current_state != new_state:
                await self.set_state(new_state)

            if new_state == DockerContainerState.STOPPED:
                break

            await asyncio.sleep(2)

    async def _monitor_logs_for_custom_states(self):
        if not self.custom_state_patterns:
            return
        
        await self.wait_for_state(DockerContainerState.RUNNING)

        container_id = await self._get_container_id_by_name()
        if not container_id:
            return

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "logs", "-f", container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            while self.current_state == DockerContainerState.RUNNING or self.current_state in self.custom_state_patterns:
                try:
                    line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
                    if not line_bytes:
                        break
                    
                    line = line_bytes.decode('utf-8', errors='ignore').strip()
                    # logger.debug(f"Log line from {self.container_name}: {line}")
                    for state, pattern in self.custom_state_patterns.items():
                        if pattern.search(line):
                            await self.set_state(state)

                except asyncio.TimeoutError:
                    if self.current_state not in [DockerContainerState.RUNNING] and self.current_state not in self.custom_state_patterns:
                        break
                    continue
                except Exception:
                    break
        except Exception:
             pass
        finally:
             if proc and proc.returncode is None:
                proc.terminate()
                await proc.wait()

    async def wait_for_state(self, state: Union[DockerContainerState, str], timeout: Optional[float] = None) -> bool:
        if self.current_state == state:
            return True
        
        async def _wait_loop():
            while self.current_state != state:
                await self._state_changed_event.wait()
        
        try:
            await asyncio.wait_for(_wait_loop(), timeout=timeout)
            return True
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False

    async def close(self):
        logger.info(f"Closing DockerRunManager for container '{self.container_name}'")
        if self.current_state not in [DockerContainerState.STOPPED, DockerContainerState.STOPPING]:
             await self.stop()
        
        await self.remove()

        for task in self._monitor_tasks:
            task.cancel()
        
        await asyncio.gather(*self._monitor_tasks, return_exceptions=True)
        self._monitor_tasks.clear()

        if self.container_name in DockerRunManager._managers:
            del DockerRunManager._managers[self.container_name]

    def __del__(self):
        if self.container_name in DockerRunManager._managers:
            del DockerRunManager._managers[self.container_name]
        
        for task in self._monitor_tasks:
            task.cancel() 
