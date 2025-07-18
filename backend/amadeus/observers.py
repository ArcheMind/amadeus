import asyncio
import copy
from hashlib import md5
from typing import Dict, Any, Protocol, List, Optional
import aiohttp

import yaml
from loguru import logger

from amadeus.common import yellow, blue, green
from amadeus.manager.docker_manager import DockerRunManager
from amadeus.manager.im.napcat import get_napcat_manager


def find_item(config_data, section_key, item_name):
    section_items = config_data.get(section_key, [])
    if not isinstance(section_items, list):
        return None

    for item in section_items:
        if isinstance(item, dict) and item.get("name") == item_name:
            return item
    return None

def embed_config(current_item, config_data):
    def resolve_and_embed(value, source_section):
        if isinstance(value, str):
            embedded_item = find_item(config_data, source_section, value)
            return embed_config(embedded_item, config_data) if embedded_item else None
        elif isinstance(value, list):
            return [resolve_and_embed(v, source_section) for v in value]
        return value

    if not current_item or not isinstance(current_item, dict):
        return current_item

    embedded_item = copy.deepcopy(current_item)
    
    for key, value in embedded_item.items():
        if key.endswith("_provider"):
            source_section = "model_providers"
            embedded_item[key] = resolve_and_embed(value, source_section)
        elif key == "character":
            source_section = "characters"
            embedded_item[key] = resolve_and_embed(value, source_section)
        elif key == "idiolect":
            source_section = "idiolects"
            embedded_item[key] = resolve_and_embed(value, source_section)
            
    return embedded_item


class ConfigObserver(Protocol):
    async def update(self, config: Dict[str, Any]) -> Dict[str, Any]:
        ...
    
    async def close(self):
        ...


class BaseConfigObserver:
    """
    Base class for config observers that synchronize external resources with configuration.
    
    This implements the view-based synchronization pattern:
    1. Extract observer-specific view from full config
    2. Read current state from external resources (config_from_state)
    3. Compare with desired state and apply changes if needed (config_to_state)
    4. Merge updated state back into full config
    """
    
    def extract_observer_config(self, full_config: Dict[str, Any]) -> Dict[str, Any]:
        """Extract observer-specific configuration view from full config."""
        raise NotImplementedError("Subclasses must implement extract_observer_config")
    
    def merge_observer_config(self, full_config: Dict[str, Any], observer_config: Dict[str, Any]) -> Dict[str, Any]:
        """Merge observer configuration back into full config."""
        raise NotImplementedError("Subclasses must implement merge_observer_config")
    
    async def config_to_state(self, config: Dict[str, Any]) -> None:
        """Apply configuration to external state (e.g., start/stop containers)."""
        raise NotImplementedError("Subclasses must implement config_to_state")
    
    async def config_from_state(self) -> Dict[str, Any]:
        """Read current configuration from external state (e.g., query running containers)."""
        raise NotImplementedError("Subclasses must implement config_from_state")
    
    async def update(self, full_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main update method that implements the synchronization pattern:
        1. Extract observer view
        2. Read current state
        3. Compare and apply changes if needed
        4. Merge updated state back
        """
        observer_name = self.__class__.__name__
        logger.debug(f"{observer_name}: Starting update")
        
        # Extract observer-specific view
        desired_config = self.extract_observer_config(full_config)
        logger.debug(f"{observer_name}: Extracted desired config: {len(desired_config.get('apps', []))} apps")
        
        # Read current state from external resources
        current_config = await self.config_from_state()
        logger.debug(f"{observer_name}: Current state: {len(current_config.get('apps', []))} apps")
        
        # Compare and apply changes if needed
        needs_update = not self._configs_equal(current_config, desired_config)
        logger.debug(f"{observer_name}: Needs update: {needs_update}")
        
        if needs_update:
            logger.info(f"{observer_name}: Applying configuration changes")
            await self.config_to_state(desired_config)
        
        # Read final state and merge back
        final_config = await self.config_from_state()
        logger.debug(f"{observer_name}: Final state: {len(final_config.get('apps', []))} apps")
        
        result = self.merge_observer_config(full_config, final_config)
        logger.debug(f"{observer_name}: Update completed")
        return result
    
    def _configs_equal(self, config1: Dict[str, Any], config2: Dict[str, Any]) -> bool:
        """Compare two configurations for equality."""
        return config1 == config2
    
    async def close(self):
        """Close observer and clean up resources."""
        pass


class IMObserver(BaseConfigObserver):
    """Observer for managing IM containers based on configuration."""
    
    def __init__(self):
        self.managed_ims: Dict[str, DockerRunManager] = {}
        self._http_session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self):
        """Ensure HTTP session is available."""
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=10.0)
            self._http_session = aiohttp.ClientSession(timeout=timeout)

    async def _check_api_and_login_status(self, manager: DockerRunManager) -> Optional[str]:
        """Check API availability and return login status. Returns None if API not ready."""
        if not manager.running:
            logger.debug(f"Container {manager.name} is not running")
            return None
        
        webui_port = manager.ports.get(6099)
        if not webui_port:
            logger.debug(f"Webui port not available for {manager.name}")
            return None
        
        try:
            await self._ensure_session()
            base_url = f"http://localhost:{webui_port}"
            
            # Get auth token
            auth_payload = {"hash": "fab552ce31e45b51288bb374b7e08d720f1d612e20fb7361246139c1e476f0b0"}
            async with self._http_session.post(f"{base_url}/api/auth/login", json=auth_payload) as response:
                if response.status != 200:
                    logger.debug(f"Auth API failed for {manager.name}: HTTP {response.status}")
                    return None
                
                data = await response.json()
                if data.get("code") != 0:
                    logger.debug(f"Auth API failed for {manager.name}: code {data.get('code')}")
                    return None
                
                token = data.get("data", {}).get("Credential")
                if not token:
                    logger.debug(f"No token received for {manager.name}")
                    return None
            
            # Check login status
            headers = {"Authorization": f"Bearer {token}"}
            async with self._http_session.post(f"{base_url}/api/QQLogin/CheckLoginStatus", headers=headers) as response:
                if response.status != 200:
                    logger.debug(f"Login status API failed for {manager.name}: HTTP {response.status}")
                    return None
                
                data = await response.json()
                if data.get("code") != 0:
                    logger.debug(f"Login status API failed for {manager.name}: code {data.get('code')}")
                    return None
                
                login_data = data.get("data", {})
                if login_data.get("isLogin", False):
                    logger.debug(f"Container {manager.name} is logged in")
                    return "ONLINE"
                elif login_data.get("qrcodeurl"):
                    logger.debug(f"Container {manager.name} has QR code for login")
                    return "LOGIN"
                else:
                    logger.debug(f"Container {manager.name} login status unclear")
                    return "starting"
                    
        except Exception as e:
            logger.debug(f"Exception checking API for {manager.name}: {e}")
            return None

    async def _wait_for_api_ready(self, manager: DockerRunManager, im_key: str, timeout: int = 60) -> None:
        """Wait until the container's login API is ready and stable."""
        logger.info(f"Waiting for API to be ready for {blue(im_key)}...")
        
        start_time = asyncio.get_event_loop().time()
        last_progress_log = start_time
        consecutive_successes = 0
        required_successes = 3  # Require 3 consecutive successful checks
        
        while True:
            current_time = asyncio.get_event_loop().time()
            if current_time - start_time > timeout:
                logger.error(f"Timeout waiting for API to be ready for {blue(im_key)} after {timeout}s")
                return
            
            # Log progress every 10 seconds
            if current_time - last_progress_log >= 10:
                elapsed = current_time - start_time
                logger.info(f"Still waiting for API for {blue(im_key)} ({elapsed:.1f}s elapsed)")
                last_progress_log = current_time
            
            # Check if container is still running
            if not manager.running:
                logger.error(f"Container {blue(im_key)} stopped while waiting for API")
                return
            
            # Try to check API and login status
            status = await self._check_api_and_login_status(manager)
            if status is not None:
                consecutive_successes += 1
                logger.debug(f"API check success {consecutive_successes}/{required_successes} for {blue(im_key)} (status: {status})")
                
                if consecutive_successes >= required_successes:
                    # API is stable and ready!
                    elapsed = current_time - start_time
                    logger.info(f"API ready and stable for {blue(im_key)} after {elapsed:.1f}s (status: {status})")
                    
                    # Log the current login status for information
                    if status == "ONLINE":
                        logger.info(f"Container {blue(im_key)} is already logged in")
                    else:
                        logger.info(f"Container {blue(im_key)} is ready for login")
                    return
            else:
                # Reset counter on failure
                if consecutive_successes > 0:
                    logger.debug(f"API check failed for {blue(im_key)}, resetting success counter")
                consecutive_successes = 0
            
            # Not ready yet, wait and retry
            await asyncio.sleep(1)

    async def _get_container_status(self, manager: DockerRunManager) -> str:
        """Get container status via HTTP API."""
        if not manager.running:
            return "stopped"
        
        # Use the shared API check method
        status = await self._check_api_and_login_status(manager)
        if status is None:
            logger.debug(f"API check returned None for {manager.name}, returning 'starting'")
        else:
            logger.debug(f"API check returned '{status}' for {manager.name}")
        return status if status is not None else "starting"

    def extract_observer_config(self, full_config: Dict[str, Any]) -> Dict[str, Any]:
        """Extract IM-specific view: only enable=true and type=NapCatQQ apps with required fields."""
        im_apps = []
        for app in full_config.get("apps", []):
            if app.get("enable", False) and app.get("type", "NapCatQQ") == "NapCatQQ":
                im_app = {
                    "name": app["name"],
                    "account": app.get("account", "default"),
                    "enable": app["enable"],
                    "type": app["type"],
                    "onebot_server": app.get("onebot_server", ""),
                    "backend_server": app.get("backend_server", ""),
                }
                im_apps.append(im_app)
        
        return {"apps": im_apps}

    def merge_observer_config(self, full_config: Dict[str, Any], observer_config: Dict[str, Any]) -> Dict[str, Any]:
        """Merge IM observer results back into full config."""
        result_config = copy.deepcopy(full_config)
        
        # Create lookup map for observer apps
        observer_apps = {app["name"]: app for app in observer_config.get("apps", [])}
        
        # Update corresponding apps in full config
        for app in result_config.get("apps", []):
            if app["name"] in observer_apps:
                observer_app = observer_apps[app["name"]]
                # Only update IM-managed fields
                app["onebot_server"] = observer_app["onebot_server"]
                app["backend_server"] = observer_app["backend_server"]
                
        return result_config

    async def config_to_state(self, config: Dict[str, Any]) -> None:
        """Apply IM configuration to docker containers."""
        logger.info(f"{yellow('--- IMObserver applying configuration ---')}")

        # Build target state map
        target_ims = {}
        for app_config in config.get("apps", []):
            if app_config.get("enable", False) and app_config.get("type", "NapCatQQ") == "NapCatQQ":
                name_hash = md5(app_config["name"].encode()).hexdigest()[:10]
                name = f"im-{name_hash}-{app_config.get('account', 'default')}"
                target_ims[name] = app_config

        current_ims = set(self.managed_ims.keys())
        target_ims_set = set(target_ims.keys())

        # Remove obsolete containers
        to_remove = current_ims - target_ims_set
        if to_remove:
            logger.info(f"Removing {len(to_remove)} IM manager(s): {', '.join(map(blue, to_remove))}")
            for im_key in to_remove:
                if im_key in self.managed_ims:
                    manager = self.managed_ims[im_key]
                    await manager.close()
                    del self.managed_ims[im_key]

        # Add new containers
        to_add = target_ims_set - current_ims
        if to_add:
            logger.info(f"Adding {len(to_add)} IM manager(s): {', '.join(map(blue, to_add))}")
            for im_key in to_add:
                app_config = target_ims[im_key]
                manager = get_napcat_manager(
                    config_name=im_key,
                    account=app_config["account"],
                    app_name=app_config["name"],
                )
                await manager.start()
                self.managed_ims[im_key] = manager
                
                # Wait until login API is available
                await self._wait_for_api_ready(manager, im_key, timeout=60)

    async def config_from_state(self) -> Dict[str, Any]:
        """Read current IM configuration from running containers."""
        current_apps = []
        
        # Get containers from Docker using the unified prefix
        containers = await DockerRunManager.get_containers_by_prefix("im-")
        
        for container in containers:
            # Extract app info from container labels and name
            labels = container.get("labels", {})
            name = container.get("name", "")
            ports = container.get("ports", {})
            running = container.get("running", False)
            
            # Extract account from container name (format: im-{hash}-{account})
            parts = name.split('-')
            account = parts[2] if len(parts) >= 3 else "default"
            
            # Get app name from labels, or derive from container name
            app_name = labels.get("amadeus.app.name", name)
            
            # Set onebot_server and backend_server for running containers
            onebot_server = ""
            backend_server = ""
            
            if running:
                onebot_port = ports.get(3001, 0)
                onebot_server = f"ws://localhost:{onebot_port}"
                backend_port = ports.get(6099, 0)
                backend_server = f"http://localhost:{backend_port}" if backend_port else ""
            
            app_config = {
                "name": app_name,
                "account": account,
                "enable": running,
                "type": "NapCatQQ",
                "onebot_server": onebot_server,
                "backend_server": backend_server,
            }
            current_apps.append(app_config)
        
        return {"apps": current_apps}

    async def close(self):
        """Close all managed IM containers and HTTP session."""
        active_ims = list(self.managed_ims.values())
        if active_ims:
            logger.info(f"Closing {len(active_ims)} IM manager(s)...")
        for manager in active_ims:
            await manager.close()
        self.managed_ims.clear()
        
        # Close HTTP session
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None


class AmadeusObserver(BaseConfigObserver):
    """Observer for managing Amadeus worker tasks based on configuration."""
    
    def __init__(self):
        self.amadeus_tasks: Dict[str, asyncio.Task] = {}
        self.task_configs: Dict[str, Dict[str, Any]] = {}  # Store config for each task
        self.watcher: Optional[asyncio.Task] = None

    def extract_observer_config(self, full_config: Dict[str, Any]) -> Dict[str, Any]:
        """Extract Amadeus-specific view: only enable=true apps with required fields."""
        amadeus_apps = []
        for app in full_config.get("apps", []):
            if app.get("enable", False):
                # Create processed config for hash calculation
                processed_app = embed_config(app, full_config)
                app_yaml = yaml.safe_dump(processed_app, allow_unicode=True, sort_keys=True)
                app_hash = md5(app_yaml.encode()).hexdigest()[:10]
                
                amadeus_app = {
                    "name": app["name"],
                    "enable": app["enable"],
                    "processed_config": processed_app,
                    "config_hash": app_hash,
                    "_process_status": app.get("_process_status", "stopped"),
                }
                amadeus_apps.append(amadeus_app)
        
        return {"apps": amadeus_apps}

    def merge_observer_config(self, full_config: Dict[str, Any], observer_config: Dict[str, Any]) -> Dict[str, Any]:
        """Merge Amadeus observer results back into full config."""
        result_config = copy.deepcopy(full_config)
        
        # Create lookup map for observer apps
        observer_apps = {app["name"]: app for app in observer_config.get("apps", [])}
        
        # Update corresponding apps in full config
        for app in result_config.get("apps", []):
            if app["name"] in observer_apps:
                observer_app = observer_apps[app["name"]]
                # Update Amadeus-managed fields
                app["_process_status"] = observer_app["_process_status"]
                
                # Auto-disable failed applications
                if observer_app["_process_status"] in ["stopped", "error"] and app.get("enable", False):
                    logger.warning(f"App {blue(app['name'])} failed to start, disabling it")
                    app["enable"] = False
                
        return result_config

    async def config_to_state(self, config: Dict[str, Any]) -> None:
        """Apply Amadeus configuration to worker tasks."""
        logger.info(f"{yellow('--- AmadeusObserver applying configuration ---')}")
        
        # Build target state map
        target_tasks = {}
        for app_config in config.get("apps", []):
            if app_config.get("enable", False):
                task_name = f"amadeus-{app_config['config_hash']}"
                target_tasks[task_name] = app_config

        current_tasks = set(self.amadeus_tasks.keys())
        target_tasks_set = set(target_tasks.keys())

        # Remove obsolete tasks
        to_remove = current_tasks - target_tasks_set
        if to_remove:
            logger.info(f"Stopping {len(to_remove)} Amadeus task(s): {', '.join(map(blue, to_remove))}")
            for task_key in to_remove:
                if task_key in self.amadeus_tasks:
                    task = self.amadeus_tasks[task_key]
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    del self.amadeus_tasks[task_key]
                    if task_key in self.task_configs:
                        del self.task_configs[task_key]

        # Add new tasks
        to_add = target_tasks_set - current_tasks
        if to_add:
            logger.info(f"Starting {len(to_add)} Amadeus task(s): {', '.join(map(blue, to_add))}")
            for task_key in to_add:
                app_config = target_tasks[task_key]
                
                # Store config for this task
                self.task_configs[task_key] = {
                    "app_name": app_config["name"],
                    "config_hash": app_config["config_hash"],
                    "processed_config": app_config["processed_config"],
                }
                
                # Create and start the task
                task = asyncio.create_task(self._run_amadeus_app(app_config["processed_config"], task_key))
                self.amadeus_tasks[task_key] = task
                logger.info(f"Amadeus task {blue(task_key)} started successfully")

        # Start task watcher if needed
        if self.amadeus_tasks and (self.watcher is None or self.watcher.done()):
            logger.info("Starting task watcher.")
            self.watcher = asyncio.create_task(self.watch_tasks())

    async def _run_amadeus_app(self, processed_config: Dict[str, Any], task_name: str):
        """
        Run an Amadeus app as an asyncio task.
        This function sets up the environment and runs the app.
        """
        import os
        import yaml
        
        try:
            # Set up environment variables for the app
            config_yaml = yaml.safe_dump(processed_config, allow_unicode=True, sort_keys=True)
            original_config = os.environ.get("AMADEUS_CONFIG")
            original_app_name = os.environ.get("AMADEUS_APP_NAME")
            
            os.environ["AMADEUS_CONFIG"] = config_yaml
            os.environ["AMADEUS_APP_NAME"] = task_name
            
            logger.info(f"Starting Amadeus app {blue(task_name)}")
            
            # Import and run the async main function
            from amadeus.app import async_main
            await async_main()
            
        except asyncio.CancelledError:
            logger.info(f"Amadeus app {blue(task_name)} was cancelled")
            raise
        except Exception as e:
            logger.error(f"Amadeus app {blue(task_name)} failed with error: {e}")
            raise
        finally:
            # Restore original environment variables
            if original_config is not None:
                os.environ["AMADEUS_CONFIG"] = original_config
            else:
                os.environ.pop("AMADEUS_CONFIG", None)
            
            if original_app_name is not None:
                os.environ["AMADEUS_APP_NAME"] = original_app_name
            else:
                os.environ.pop("AMADEUS_APP_NAME", None)

    async def config_from_state(self) -> Dict[str, Any]:
        """Read current Amadeus configuration from actual running tasks."""
        current_apps = []
        
        # Check all managed tasks and their real states
        for task_key, task in self.amadeus_tasks.items():
            # Get task info from stored config
            task_config = self.task_configs.get(task_key, {})
            app_name = task_config.get("app_name", f"amadeus_app_{task_key.replace('amadeus-', '')}")
            config_hash = task_config.get("config_hash", task_key.replace("amadeus-", ""))
            processed_config = task_config.get("processed_config")
            
            # Determine task status
            if task.done():
                try:
                    await task  # This will raise the exception if task failed
                    task_status = "stopped"  # Task completed successfully
                except asyncio.CancelledError:
                    task_status = "stopped"  # Task was cancelled
                except Exception:
                    task_status = "error"  # Task failed with exception
            elif task.cancelled():
                task_status = "stopped"
            else:
                task_status = "running"
            
            app_config = {
                "name": app_name,
                "enable": True,
                "config_hash": config_hash,
                "_process_status": task_status,
                "processed_config": processed_config,
            }
            current_apps.append(app_config)
        
        return {"apps": current_apps}

    async def watch_tasks(self):
        """Watch worker tasks and clean up failed ones."""
        while True:
            try:
                for task_key, task in list(self.amadeus_tasks.items()):
                    if task.done():
                        try:
                            await task  # This will raise the exception if task failed
                            logger.info(f"Amadeus task {blue(task_key)} completed successfully")
                        except asyncio.CancelledError:
                            logger.info(f"Amadeus task {blue(task_key)} was cancelled")
                        except Exception as e:
                            logger.error(f"Amadeus task {blue(task_key)} failed with error: {e}")
                        
                        # Remove completed/failed tasks
                        del self.amadeus_tasks[task_key]
                        if task_key in self.task_configs:
                            del self.task_configs[task_key]
                
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                logger.info("Task watcher cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in task watcher: {e}")
    
    async def close(self):
        """Close all managed Amadeus tasks."""
        if self.watcher:
            self.watcher.cancel()
            try:
                await self.watcher
            except asyncio.CancelledError:
                pass
        
        active_tasks = list(self.amadeus_tasks.values())
        if active_tasks:
            logger.info(f"Closing {len(active_tasks)} Amadeus task(s)...")
        
        # Cancel all tasks
        for task in active_tasks:
            task.cancel()
        
        # Wait for all tasks to complete
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        
        self.amadeus_tasks.clear()
        self.task_configs.clear()
