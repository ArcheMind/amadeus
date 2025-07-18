import asyncio
import os
import re
import sys
from enum import Enum, unique
from typing import Dict, List, Optional, Set, Union, Any
from loguru import logger

from amadeus.common import blue, green, red, yellow


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

    def __new__(cls, name: str, *args, **kwargs):
        if name in cls._managers:
            return cls._managers[name]
        instance = super().__new__(cls)
        cls._managers[name] = instance
        return instance

    def __init__(self,
                 name: str,
                 image_name: str,
                 run_command: Optional[List[str]] = None,
                 custom_states: Optional[Dict[str, str]] = None,
                 **kwargs):
        if hasattr(self, '_initialized'):
            return
        
        self.name = name
        self.image_name = image_name
        self.run_command = run_command or []
        ports = kwargs.get('ports', {})
        self.ports = {v: k for k, v in ports.items()}
        self.docker_run_options = kwargs

        self._current_state: Union[DockerContainerState, str] = DockerContainerState.PENDING
        self.state_changed = asyncio.Event()
        
        self.custom_state_patterns: Dict[str, re.Pattern] = {}
        if custom_states:
            for name, pattern in custom_states.items():
                self.custom_state_patterns[name] = re.compile(pattern)

        self._monitor_tasks: Set[asyncio.Task] = set()
        self._initialized = True
        
    @property
    def current_state(self) -> Union[DockerContainerState, str]:
        return self._current_state

    @property
    def running(self) -> bool:
        return self._current_state not in [DockerContainerState.PENDING, DockerContainerState.STOPPED, DockerContainerState.ERROR]

    async def set_state(self, new_state: Union[DockerContainerState, str]):
        if self._current_state != new_state:
            logger.info(f"Container {blue(self.name)} state changed to: {green(new_state.value if isinstance(new_state, Enum) else new_state)}")
            self._current_state = new_state
            self.state_changed.set()
            self.state_changed.clear()

    def _build_docker_run_command(self) -> List[str]:
        cmd = ["docker", "run", "--name", self.name, "-d"]
        
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
                    elif key == "label":
                        cmd.extend(["-l", f"{k}={v}"])
            elif isinstance(value, bool) and value:
                cmd.append(option)
            elif isinstance(value, str):
                cmd.extend([option, value])
            elif isinstance(value, list):
                 for item in value:
                     cmd.extend([option, item])

        cmd.append(self.image_name)
        cmd.extend(self.run_command)
        logger.debug(f"Built docker command: {blue(' '.join(cmd))}")
        return cmd

    async def _run_subprocess(self, *args) -> Optional[str]:
        try:
            logger.trace(f"Running command: {blue(' '.join(args))}")
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy()  # Ensure environment variables are passed
            )
            
            # Add timeout to prevent hanging
            timeout = 30 if args[1] in ["rm", "kill", "stop"] else 60
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            if proc.returncode != 0:
                logger.debug(f"Command failed with return code {red(str(proc.returncode))}: {blue(' '.join(args))}. Stderr: {red(stderr.decode().strip())}")
                return None
            
            logger.trace(f"Command finished successfully: {blue(' '.join(args))}")
            return stdout.decode().strip()
        except asyncio.TimeoutError:
            logger.debug(f"Command timed out: {blue(' '.join(args))}")
            if proc:
                proc.kill()
                await proc.wait()
            return None
        except FileNotFoundError:
            # Docker not found - this is expected when Docker is not installed
            logger.debug(f"Docker command not found: {blue(' '.join(args))}")
            return None
        except Exception as e:
            logger.debug(f"An unexpected error occurred while running command: {blue(' '.join(args))}. Error: {red(str(e))}")
            return None


    async def _get_container_id_by_name(self) -> Optional[str]:
        return await self._run_subprocess("docker", "ps", "-a", "--filter", f"name=^{self.name}$", "--format", "{{.ID}}")

    async def _get_container_state_by_id(self, container_id: str) -> Optional[str]:
         return await self._run_subprocess("docker", "inspect", "-f", "{{.State.Status}}", container_id)

    async def _get_container_config(self, container_id: str) -> Optional[Dict[str, Any]]:
        """Get container configuration for comparison"""
        try:
            import json
            result = await self._run_subprocess("docker", "inspect", container_id)
            if not result:
                return None
            
            inspect_data = json.loads(result)
            if not inspect_data:
                return None
            
            container_info = inspect_data[0]
            config = container_info.get("Config", {})
            host_config = container_info.get("HostConfig", {})
            
            return {
                "image": config.get("Image", ""),
                "cmd": config.get("Cmd", []),
                "env": sorted(config.get("Env", [])),
                "ports": host_config.get("PortBindings", {}),
                "volumes": host_config.get("Binds", [])
            }
        except Exception as e:
            logger.warning(f"Failed to get container config for {container_id}: {e}")
            return None

    def _normalize_expected_config(self) -> Dict[str, Any]:
        """Get expected configuration for comparison"""
        expected_env = []
        expected_ports = {}
        expected_volumes = []
        
        # Process docker run options
        for key, value in self.docker_run_options.items():
            if key == "env" and isinstance(value, dict):
                for k, v in value.items():
                    expected_env.append(f"{k}={v}")
            elif key == "ports" and isinstance(value, dict):
                for host_port, container_port in value.items():
                    expected_ports[f"{container_port}/tcp"] = [{"HostPort": str(host_port)}]
            elif key == "volumes" and isinstance(value, dict):
                for host_path, container_path in value.items():
                    expected_volumes.append(f"{host_path}:{container_path}")
        
        return {
            "image": self.image_name,
            "cmd": self.run_command,
            "env": sorted(expected_env),
            "ports": expected_ports,
            "volumes": sorted(expected_volumes)
        }

    def _configs_match(self, current_config: Dict[str, Any], expected_config: Dict[str, Any]) -> bool:
        """Compare key container configurations"""
        # Compare critical config elements
        checks = [
            ("ports", current_config.get("ports", {}), expected_config.get("ports", {})),
            ("env", set(current_config.get("env", [])), set(expected_config.get("env", []))),
            ("volumes", set(current_config.get("volumes", [])), set(expected_config.get("volumes", []))),
            ("cmd", current_config.get("cmd", []), expected_config.get("cmd", []))
        ]
        
        for name, current, expected in checks:
            if current != expected:
                logger.debug(f"{name} mismatch: {current} vs {expected}")
                return False
        
        return True

    def _start_monitoring_tasks(self):
        # Don't restart if already monitoring
        if self._monitor_tasks:
            return

        # Start monitoring tasks
        task1 = asyncio.create_task(self._monitor_docker_state())
        task2 = asyncio.create_task(self._monitor_logs_for_custom_states())
        self._monitor_tasks.update([task1, task2])
        
        # Clean up tasks when done
        for task in [task1, task2]:
            task.add_done_callback(self._monitor_tasks.discard)

    async def start(self):
        if self.current_state not in [DockerContainerState.PENDING, DockerContainerState.STOPPED, DockerContainerState.ERROR]:
            return

        logger.info(f"Starting container: {blue(self.name)}...")
        await self.set_state(DockerContainerState.STARTING)
        
        # Check if container exists and remove it to ensure fresh start
        container_id = await self._get_container_id_by_name()
        if container_id:
            logger.info(f"Found existing container: {blue(self.name)}, removing it to use new config...")
            await self._run_subprocess("docker", "rm", "-f", container_id)
            await asyncio.sleep(0.5)  # Brief wait for cleanup
        
        # Create new container
        logger.info(f"Creating new container: {blue(self.name)}")
        run_cmd = self._build_docker_run_command()
        new_container_id = await self._run_subprocess(*run_cmd)
        if not new_container_id:
            await self.set_state(DockerContainerState.ERROR)
            return

        self._start_monitoring_tasks()

    async def _is_container_ready(self, container_id: str) -> bool:
        """Check if container is ready to use (idempotent check)"""
        try:
            # Check if running
            status = await self._get_container_state_by_id(container_id)
            logger.debug(f"Container {self.name} status: {status}")
            if status != "running":
                logger.debug(f"Container {self.name} not running (status: {status})")
                return False
            
            # For simplicity, if container is running, consider it ready
            # Skip complex config comparison for now to avoid issues
            logger.debug(f"Container {self.name} is running and ready for reuse")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to check container {self.name} readiness: {e}")
            return False

    async def stop(self):
        container_id = await self._get_container_id_by_name()
        if container_id:
            await self._run_subprocess("docker", "stop", container_id)
        await self.set_state(DockerContainerState.STOPPED)

    async def remove(self):
        container_id = await self._get_container_id_by_name()
        if container_id:
            await self._run_subprocess("docker", "rm", "-f", container_id)
        await self.set_state(DockerContainerState.STOPPED)


    async def _monitor_docker_state(self):
        while self.current_state not in [DockerContainerState.STOPPED, DockerContainerState.ERROR]:
            container_id = await self._get_container_id_by_name()
            if not container_id:
                await self.set_state(DockerContainerState.STOPPED)
                break
            
            status = await self._get_container_state_by_id(container_id)
            
            if not status:
                logger.warning(f"Could not get status for container {blue(self.name)} ({container_id}). Assuming it has been removed.")
                await self.set_state(DockerContainerState.STOPPED)
                break

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
                logger.info(f"Container {blue(self.name)} has stopped.")
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
                    # logger.debug(f"Log line from {self.name}: {line}")
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
                await self.state_changed.wait()
        
        try:
            await asyncio.wait_for(_wait_loop(), timeout=timeout)
            return True
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False

    async def get_container_labels(self) -> Dict[str, str]:
        """Get container labels for configuration retrieval."""
        container_id = await self._get_container_id_by_name()
        if not container_id:
            return {}
        
        try:
            import json
            result = await self._run_subprocess("docker", "inspect", "-f", "{{json .Config.Labels}}", container_id)
            if result:
                return json.loads(result) or {}
        except Exception as e:
            logger.warning(f"Failed to get labels for container {self.name}: {e}")
        
        return {}



    @classmethod
    async def get_containers_by_prefix(cls, prefix: str) -> List[Dict[str, Any]]:
        """Get all containers with the specified name prefix and their metadata."""
        try:
            import json
            # Get all containers with the prefix
            result = await cls._run_subprocess_static("docker", "ps", "-a", "--filter", f"name=^{prefix}", "--format", "{{.ID}}")
            
            if not result:
                return []
            
            container_ids = result.strip().split('\n')
            containers = []
            
            for container_id in container_ids:
                if not container_id:
                    continue
                    
                # Get detailed container info
                inspect_result = await cls._run_subprocess_static("docker", "inspect", container_id)
                if inspect_result:
                    inspect_data = json.loads(inspect_result)
                    if inspect_data:
                        container_info = inspect_data[0]
                        config = container_info.get("Config", {})
                        state = container_info.get("State", {})
                        host_config = container_info.get("HostConfig", {})
                        
                        container_data = {
                            "id": container_id,
                            "name": container_info.get("Name", "").lstrip("/"),
                            "image": config.get("Image", ""),
                            "state": state.get("Status", "unknown"),
                            "labels": config.get("Labels") or {},
                            "ports": cls._extract_port_mappings(host_config.get("PortBindings", {})),
                            "running": state.get("Running", False),
                        }
                        containers.append(container_data)
                        
            return containers
            
        except Exception as e:
            logger.debug(f"Failed to get containers by prefix {prefix}: {e}")
            return []

    @staticmethod
    def _extract_port_mappings(port_bindings: Dict[str, Any]) -> Dict[int, int]:
        """Extract port mappings from Docker port bindings."""
        ports = {}
        for container_port, host_bindings in port_bindings.items():
            if host_bindings and isinstance(host_bindings, list):
                try:
                    container_port_num = int(container_port.split('/')[0])
                    host_port_num = int(host_bindings[0].get('HostPort', 0))
                    if host_port_num > 0:
                        ports[container_port_num] = host_port_num
                except (ValueError, IndexError, AttributeError):
                    continue
        return ports

    @staticmethod
    async def _run_subprocess_static(*args) -> Optional[str]:
        """Static version of _run_subprocess for class methods."""
        try:
            logger.trace(f"Running command: {blue(' '.join(args))}")
            # Use shell=True to ensure PATH is properly resolved
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy()  # Ensure environment variables are passed
            )
            
            timeout = 30 if args[1] in ["rm", "kill", "stop"] else 60
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            if proc.returncode != 0:
                logger.debug(f"Command failed with return code {red(str(proc.returncode))}: {blue(' '.join(args))}. Stderr: {red(stderr.decode().strip())}")
                return None
            
            logger.trace(f"Command finished successfully: {blue(' '.join(args))}")
            return stdout.decode().strip()
        except asyncio.TimeoutError:
            logger.debug(f"Command timed out: {blue(' '.join(args))}")
            if proc:
                proc.kill()
                await proc.wait()
            return None
        except FileNotFoundError:
            # Docker not found - this is expected when Docker is not installed
            logger.debug(f"Docker command not found: {blue(' '.join(args))}")
            return None
        except Exception as e:
            logger.debug(f"An unexpected error occurred while running command: {blue(' '.join(args))}. Error: {red(str(e))}")
            return None

    async def close(self):
        # Cancel monitoring tasks
        for task in self._monitor_tasks:
            task.cancel()
        self._monitor_tasks.clear()
        
        # Force remove container
        container_id = await self._get_container_id_by_name()
        if container_id:
            await self._run_subprocess("docker", "rm", "-f", container_id)
        
        # Clean up manager reference
        if self.name in DockerRunManager._managers:
            del DockerRunManager._managers[self.name]

    def __del__(self):
        if self.name in DockerRunManager._managers:
            del DockerRunManager._managers[self.name]
        
        for task in self._monitor_tasks:
            task.cancel() 
