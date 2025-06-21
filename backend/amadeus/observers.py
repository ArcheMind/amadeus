import asyncio
import copy
from hashlib import md5
import yaml
from typing import Dict, Any, Protocol, List, Optional, Set
from loguru import logger

from amadeus.common import yellow, blue, green
from amadeus.manager.docker_manager import DockerRunManager
from amadeus.manager.im.napcat import get_napcat_manager
from amadeus.manager.multiprocess_manager import MultiprocessManager, MultiprocessState
from amadeus.worker import run_amadeus_app_target

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

def digest_config_data(config_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Flattens and processes config data for easier use.
    It fully resolves and embeds all nested configurations for each app.
    """
    apps = config_data.get("apps", [])
    
    processed_apps = []
    for app in apps:
        processed_app = embed_config(app, config_data)
        if processed_app:
            processed_apps.append(processed_app)
            
    return processed_apps


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
        logger.debug(f"{observer_name} update() starting")
        
        # Extract observer-specific view
        desired_config = self.extract_observer_config(full_config)
        logger.debug(f"{observer_name} extracted desired config: {desired_config}")
        
        # Read current state from external resources
        current_config = await self.config_from_state()
        logger.debug(f"{observer_name} read current config: {current_config}")
        
        # Compare and apply changes if needed
        if not self._configs_equal(current_config, desired_config):
            logger.debug(f"{observer_name} configs differ, applying changes")
            await self.config_to_state(desired_config)
        else:
            logger.debug(f"{observer_name} configs are equal, no changes needed")
        
        # Read final state and merge back
        final_config = await self.config_from_state()
        logger.debug(f"{observer_name} final config from state: {final_config}")
        
        result_config = self.merge_observer_config(full_config, final_config)
        logger.debug(f"{observer_name} merged result config apps count: {len(result_config.get('apps', []))}")
        
        return result_config
    
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

    def extract_observer_config(self, full_config: Dict[str, Any]) -> Dict[str, Any]:
        """Extract IM-specific view: only managed=true apps with required fields."""
        im_apps = []
        for app in full_config.get("apps", []):
            if app.get("managed", False):
                # Ensure required fields are not empty strings (schema validation)
                app_name = app.get("name", "")
                if not app_name or app_name.strip() == "":
                    logger.warning(f"IMObserver: skipping app with empty name: {app}")
                    continue
                    
                account = app.get("account", "default")
                if not account or account.strip() == "":
                    account = "default"
                
                im_app = {
                    "name": app_name,
                    "account": account,
                    "managed": app["managed"],
                    "onebot_server": app.get("onebot_server", ""),
                    "_connection_status": app.get("_connection_status", "disconnected"),
                }
                im_apps.append(im_app)
        
        return {"apps": im_apps}

    def merge_observer_config(self, full_config: Dict[str, Any], observer_config: Dict[str, Any]) -> Dict[str, Any]:
        """Merge IM observer results back into full config."""
        result_config = copy.deepcopy(full_config)
        
        # Create lookup map for observer apps
        observer_apps = {app["name"]: app for app in observer_config.get("apps", [])}
        logger.debug(f"IMObserver merge: observer apps map: {list(observer_apps.keys())}")
        
        # Update corresponding apps in full config
        updated_apps = []
        for app in result_config.get("apps", []):
            if app["name"] in observer_apps:
                observer_app = observer_apps[app["name"]]
                # Only update IM-managed fields
                old_onebot = app.get("onebot_server", "")
                old_status = app.get("_connection_status", "")
                
                app["onebot_server"] = observer_app["onebot_server"]
                app["_connection_status"] = observer_app["_connection_status"]
                
                logger.debug(f"IMObserver merge: updated app {blue(app['name'])}: onebot_server '{old_onebot}' -> '{app['onebot_server']}', status '{old_status}' -> '{app['_connection_status']}'")
                
                # Check for empty strings that might cause validation errors
                empty_fields = [k for k, v in app.items() if v == ""]
                if empty_fields:
                    logger.warning(f"IMObserver merge: app {blue(app['name'])} has empty fields: {empty_fields}")
                
                updated_apps.append(app["name"])
                
        logger.debug(f"IMObserver merge: updated {len(updated_apps)} apps: {updated_apps}")
        return result_config

    async def config_to_state(self, config: Dict[str, Any]) -> None:
        """Apply IM configuration to docker containers."""
        logger.info(f"{yellow('--- IMObserver applying configuration ---')}")

        # Build target state map
        target_ims = {}
        for app_config in config.get("apps", []):
            if app_config.get("managed", False):
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
                
                # Wait for connection
                try:
                    await asyncio.sleep(0.2)
                    while manager.current_state not in ["LOGIN", "ONLINE"] and manager.running:
                        await manager.state_changed.wait()
                except Exception as e:
                    logger.error(f"Error waiting for IM manager {blue(im_key)}: {e}")

    async def config_from_state(self) -> Dict[str, Any]:
        """Read current IM configuration from running containers."""
        current_apps = []
        
        # Get containers from Docker using the unified prefix
        containers = await DockerRunManager.get_containers_by_prefix("im-")
        logger.debug(f"IMObserver config_from_state found {len(containers)} containers")
        
        for container in containers:
            # Extract app info from container labels and name
            labels = container.get("labels", {})
            name = container.get("name", "")
            ports = container.get("ports", {})
            running = container.get("running", False)
            state = container.get("state", "unknown")
            
            # Extract account from container name (format: im-{hash}-{account})
            parts = name.split('-')
            account = parts[2] if len(parts) >= 3 and parts[2] else "default"
            # Ensure account is not empty (required by schema minLength: 1)  
            if not account or account.strip() == "":
                account = "default"
            
            # Get app name from labels, or derive from container name
            app_name = labels.get("amadeus.app.name", name)
            # Ensure app_name is not empty (required by schema minLength: 1)
            if not app_name or app_name.strip() == "":
                app_name = name if name else f"container_{container.get('id', 'unknown')[:8]}"
            
            # Determine OneBot server URL
            onebot_port = ports.get(3001, 0)
            onebot_server = f"ws://localhost:{onebot_port}" if onebot_port > 0 and running else ""
            
            # Determine connection status
            connection_status = "disconnected"
            if running:
                if name in self.managed_ims:
                    manager = self.managed_ims[name]
                    if manager.current_state in ["LOGIN", "ONLINE"]:
                        connection_status = "connected"
                    else:
                        connection_status = "connecting"
                else:
                    connection_status = "unknown"
            
            app_config = {
                "name": app_name,
                "account": account,
                "managed": True,
                "onebot_server": onebot_server,
                "_connection_status": connection_status,
            }
            
            # Log each generated config for debugging
            logger.debug(f"IMObserver generated config for container {blue(name)}: {app_config}")
            
            # Check for empty strings that might cause validation errors
            empty_fields = [k for k, v in app_config.items() if v == ""]
            if empty_fields:
                logger.warning(f"IMObserver found empty fields in config for container {blue(name)}: {empty_fields}")
            
            current_apps.append(app_config)
        
        logger.debug(f"IMObserver config_from_state returning {len(current_apps)} apps")
        return {"apps": current_apps}

    async def close(self):
        """Close all managed IM containers."""
        active_ims = list(self.managed_ims.values())
        if active_ims:
            logger.info(f"Closing {len(active_ims)} IM manager(s)...")
        for manager in active_ims:
            await manager.close()
        self.managed_ims.clear()


class AmadeusObserver(BaseConfigObserver):
    """Observer for managing Amadeus worker processes based on configuration."""
    
    def __init__(self):
        self.amadeus_workers: Dict[str, MultiprocessManager] = {}
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
                # Only update Amadeus-managed fields
                app["_process_status"] = observer_app["_process_status"]
                
        return result_config

    async def config_to_state(self, config: Dict[str, Any]) -> None:
        """Apply Amadeus configuration to worker processes."""
        logger.info(f"{yellow('--- AmadeusObserver applying configuration ---')}")
        
        # Build target state map
        target_workers = {}
        for app_config in config.get("apps", []):
            if app_config.get("enable", False):
                worker_name = f"amadeus-{app_config['config_hash']}"
                target_workers[worker_name] = app_config

        current_workers = set(self.amadeus_workers.keys())
        target_workers_set = set(target_workers.keys())

        # Remove obsolete workers
        to_remove = current_workers - target_workers_set
        if to_remove:
            logger.info(f"Stopping {len(to_remove)} Amadeus worker(s): {', '.join(map(blue, to_remove))}")
            for worker_key in to_remove:
                if worker_key in self.amadeus_workers:
                    manager = self.amadeus_workers[worker_key]
                    await manager.close()
                    del self.amadeus_workers[worker_key]

        # Add new workers
        to_add = target_workers_set - current_workers
        if to_add:
            logger.info(f"Starting {len(to_add)} Amadeus worker(s): {', '.join(map(blue, to_add))}")
            for worker_key in to_add:
                app_config = target_workers[worker_key]
                app_yaml = yaml.safe_dump(app_config["processed_config"], allow_unicode=True, sort_keys=True)

                manager = MultiprocessManager(
                    name=worker_key,
                    target=run_amadeus_app_target,
                    args=(app_yaml, worker_key),
                    stream_logs=True,
                )
                await manager.start()
                await asyncio.sleep(3)

                if manager.current_state == MultiprocessState.RUNNING:
                    self.amadeus_workers[worker_key] = manager
                else:
                    await manager.close()
                    logger.error(f"Failed to start Amadeus worker {blue(worker_key)}")

        # Start process watcher if needed
        if self.amadeus_workers and (self.watcher is None or self.watcher.done()):
            logger.info("Starting process watcher.")
            self.watcher = asyncio.create_task(self.watch_processes())

    async def config_from_state(self) -> Dict[str, Any]:
        """Read current Amadeus configuration from running processes."""
        current_apps = []
        
        for worker_key, manager in self.amadeus_workers.items():
            # Extract app info from worker name
            # Format: amadeus-{hash}
            config_hash = worker_key.replace("amadeus-", "")
            
            app_config = {
                "name": f"app_for_{worker_key}",  # This should be improved
                "enable": True,
                "processed_config": {},  # Placeholder since we can't reconstruct original config
                "config_hash": config_hash,
                "_process_status": "running" if manager.current_state == MultiprocessState.RUNNING else "stopped",
            }
            current_apps.append(app_config)
        
        return {"apps": current_apps}

    async def watch_processes(self):
        """Watch worker processes and clean up failed ones."""
        while True:
            try:
                for process_hash, manager in list(self.amadeus_workers.items()):
                    if manager.current_state != MultiprocessState.RUNNING:
                        logger.warning(f"Amadeus worker {blue(manager.name)} is no longer running. Removing it.")
                        await manager.close()
                        if process_hash in self.amadeus_workers:
                            del self.amadeus_workers[process_hash]
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                logger.info("Process watcher cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in process watcher: {e}")
    
    async def close(self):
        """Close all managed Amadeus workers."""
        if self.watcher:
            self.watcher.cancel()
            try:
                await self.watcher
            except asyncio.CancelledError:
                pass
        
        active_processes = list(self.amadeus_workers.values())
        if active_processes:
            logger.info(f"Closing {len(active_processes)} Amadeus worker(s)...")
        for manager in active_processes:
            await manager.close()
        
        self.amadeus_workers.clear()
