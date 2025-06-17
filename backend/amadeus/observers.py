import asyncio
import copy
from hashlib import md5
import yaml
from typing import Dict, Any, Protocol, List, Optional
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

class IMObserver:
    def __init__(self):
        self.managed_ims: Dict[str, DockerRunManager] = {}

    async def update(self, config: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"{yellow('--- IMObserver applying configuration ---')}")

        im_info_map = {}
        for app_config in config.get("apps", []):
            if app_config.get("managed", False):
                name_hash = md5(app_config["name"].encode()).hexdigest()[:10]
                name = f"im-{name_hash}-{app_config.get('account', 'default')}"
                im_info_map[name] = {
                    "name": name,
                    "config": app_config,  # Direct reference to original app config
                }

        prev_ims = set(self.managed_ims.keys())
        current_ims = set(im_info_map.keys())

        to_remove_ims = prev_ims - current_ims
        to_add_ims = current_ims - prev_ims

        if to_remove_ims:
            logger.info(f"Removing {len(to_remove_ims)} IM manager(s): {', '.join(map(blue, to_remove_ims))}")
            for im_key in to_remove_ims:
                if im_key in self.managed_ims:
                    manager = self.managed_ims[im_key]
                    await manager.close()
                    del self.managed_ims[im_key]

        if to_add_ims:
            logger.info(f"Adding {len(to_add_ims)} IM manager(s): {', '.join(map(blue, to_add_ims))}")
            for im_key in to_add_ims:
                app_ref = im_info_map[im_key]["config"]  # Direct reference
                manager = get_napcat_manager(
                    config_name=im_key,
                    account=app_ref["account"],
                )
                await manager.start()
                self.managed_ims[im_key] = manager
                try:
                    await asyncio.sleep(0.2)
                    while manager.current_state not in ["LOGIN", "ONLINE"] and manager.running:
                        await manager.state_changed.wait()

                    if not manager.running:
                        logger.error(f"Failed to start managed IM {blue(im_key)}. It has been removed.")
                        await manager.close()
                        del self.managed_ims[im_key]
                        # Update config to reflect failure, using the direct reference
                        app_ref["managed"] = False
                        app_ref["_managed_status"] = "Failed to start"
                    else:
                        # Update config on success, using the direct reference
                        app_ref["onebot_server"] = f"ws://localhost:{manager.ports[3001]}"
                        logger.info(f"Managed IM {blue(im_key)} started. App {blue(app_ref['name'])} configured with onebot_server {app_ref['onebot_server']}")
                finally:
                    pass
        return config

    async def close(self):
        active_ims = list(self.managed_ims.values())
        if active_ims:
            logger.info(f"Closing {len(active_ims)} IM manager(s)...")
        for manager in active_ims:
            await manager.close()
        self.managed_ims.clear()


class AmadeusObserver:
    def __init__(self):
        self.amadeus_workers: Dict[str, MultiprocessManager] = {}
        self.watcher: Optional[asyncio.Task] = None

    async def update(self, config: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"{yellow('--- AmadeusObserver applying configuration ---')}")

        app_info_map = {}
        for original_app_config in config.get("apps", []):
            if original_app_config.get("enable", False):
                processed_app_config = embed_config(original_app_config, config)
                app_yaml = yaml.safe_dump(processed_app_config, allow_unicode=True, sort_keys=True)
                app_hash = md5(app_yaml.encode()).hexdigest()[:10]
                name = f"amadeus-{app_hash}"
                app_info_map[name] = {
                    "name": name,
                    "original_config": original_app_config,
                    "processed_yaml": app_yaml,
                }

        prev_process_hashes = set(self.amadeus_workers.keys())
        current_app_hashes = set(app_info_map.keys())

        to_remove_amadeus = prev_process_hashes - current_app_hashes
        to_add_amadeus = current_app_hashes - prev_process_hashes

        if to_remove_amadeus:
            logger.info(f"Stopping {len(to_remove_amadeus)} Amadeus worker(s): {', '.join(map(blue, to_remove_amadeus))}")
            for app_hash in to_remove_amadeus:
                if app_hash in self.amadeus_workers:
                    manager = self.amadeus_workers[app_hash]
                    await manager.close()
                    del self.amadeus_workers[app_hash]

        if to_add_amadeus:
            logger.info(f"Starting {len(to_add_amadeus)} Amadeus worker(s): {', '.join(map(blue, to_add_amadeus))}")
            for app_hash in to_add_amadeus:
                app_detail = app_info_map[app_hash]
                app_name = app_detail["name"]
                app_yaml = app_detail["processed_yaml"]
                original_app_ref = app_detail["original_config"]

                manager = MultiprocessManager(
                    name=app_name,
                    target=run_amadeus_app_target,
                    args=(app_yaml, app_name),
                    stream_logs=True,
                )
                await manager.start()

                await asyncio.sleep(3)

                if manager.current_state != MultiprocessState.RUNNING:
                    await manager.close()
                    logger.error(f"Failed to start Amadeus worker {blue(app_name)}. It has been disabled.")

                    original_app_ref["enable"] = False
                else:
                    self.amadeus_workers[app_hash] = manager

        if self.watcher is None or self.watcher.done():
            logger.info("Process watcher is not running. Starting it now.")
            self.watcher = asyncio.create_task(self.watch_processes())
        return config

    async def watch_processes(self):
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
