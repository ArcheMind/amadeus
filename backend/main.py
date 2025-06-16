import os
from hashlib import md5
import logging
from loguru import logger
import sys
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional

import asyncio
import multiprocessing
import copy
import fastapi
import socket
from amadeus.common import green
from amadeus.config_schema import CONFIG_SCHEMA, EXAMPLE_CONFIG
from amadeus.config_router import ConfigRouter
from amadeus.config_persistence import ConfigPersistence
from amadeus.manager.multiprocess_manager import MultiprocessManager, MultiprocessState
from amadeus.manager.docker_manager import DockerRunManager
from amadeus.manager.im.napcat import get_napcat_manager
import yaml
from fastapi.middleware.cors import CORSMiddleware


# 设置multiprocessing使用spawn方法
multiprocessing.set_start_method('spawn', force=True)


def run_amadeus_app_target(config_yaml: str, app_name: str):
    """
    在子进程中运行amadeus app的函数
    """
    # 设置环境变量
    os.environ["AMADEUS_CONFIG"] = config_yaml
    os.environ["AMADEUS_APP_NAME"] = app_name
    
    # 在子进程中重新配置loguru，确保日志通过stdout发送到父进程的队列中
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        enqueue=True,  # 使用队列来确保进程安全
        backtrace=True, # 记录异常回溯
        colorize=True
    )
    
    # 启动amadeus app
    from amadeus.app import main

    try:
        main()
    except Exception as e:
        # 使用配置好的logger来记录异常，它会被发送到父进程
        logger.exception(f"An exception occurred in amadeus.app.main for '{app_name}': {e}")
        # 抛出异常以确保子进程以错误代码退出
        raise


@asynccontextmanager
async def lifespan(_: fastapi.FastAPI):
    """
    Lifespan event handler for FastAPI to manage startup and shutdown events.
    """
    try:
        # Startup event
        logger.info("Application startup: Initializing services.")
        config_data = config_persistence.load()
        # Avoid logging full config_data if it's too verbose or contains sensitive info
        # logger.debug(f"Initial configuration data: {config_data}")
        config_data, _ = await WorkerManager.apply_config(config_data)
        logger.info("Application startup: Services initialized.")
        yield  # Yield control back to the FastAPI app
    finally:
        # Shutdown event
        logger.info("Application shutdown: Terminating all services.")
        if WorkerManager.watcher:
            WorkerManager.watcher.cancel()
            try:
                await WorkerManager.watcher
            except asyncio.CancelledError:
                logger.info("Process watcher task cancelled.")

        active_processes = list(WorkerManager.amadeus_workers.values())
        for manager in active_processes:
            logger.info(f"Shutting down service '{manager.name}'.")
            await manager.close()

        active_ims = list(WorkerManager.managed_ims.values())
        for manager in active_ims:
            logger.info(f"Shutting down im service '{manager.name}'.")
            await manager.close()

        WorkerManager.amadeus_workers.clear()  # Clear out the process map
        WorkerManager.managed_ims.clear()
        logger.info(
            "Application shutdown: All services terminated. WorkerManager shutdown complete."
        )


app = fastapi.FastAPI(
    title="Amadeus Configuration API",
    description="API for managing Amadeus configuration.",
    version="1.0.0",
    lifespan=lifespan,
)

# Set up CORS middleware
origins = [
    "http://localhost:5173",  # Frontend dev server
    "http://127.0.0.1:5173",
    "file://",  # Allow file:// protocol for production
    "http://localhost:*",  # Allow any localhost port
    "http://127.0.0.1:*",  # Allow any localhost port
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the config persistence
config_persistence = ConfigPersistence(EXAMPLE_CONFIG)


async def save_config_data(config_data: dict):
    logger.info("Updating configuration data.")
    modified_config_data, config_changed = await WorkerManager.apply_config(config_data)
    config_persistence.save(modified_config_data)
    return config_changed


# Initialize the config router with schema and data getter/setter
config_router = ConfigRouter(
    config_schema=CONFIG_SCHEMA,
    data_getter=config_persistence.load,
    data_setter=save_config_data,
)


async def app_enhancer(
    schema: Dict[str, Any],
    config_data: Dict[str, Any],
    class_name: str,
    instance_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Enhance schema by resolving joined groups.
    This is a placeholder for future implementation.
    """
    # Placeholder for joined group logic
    # Find instance in list
    logger.debug(
        f"Enhancing schema for class '{class_name}' with instance '{instance_name}'"
    )
    instances = config_data.get(class_name, [])
    if not instance_name:
        return schema

    for instance in instances:
        if instance.get("name") == instance_name:
            break
    else:
        return schema

    schema = copy.deepcopy(schema)

    send_port = instance.get("send_port")

    if instance.get("managed", False):
        # If the instance is managed, we need to get the IM service manager
        im_name = f"im-{md5(instance_name.encode()).hexdigest()[:10]}-{instance.get('account', 'default')}"
        im_manager = WorkerManager.managed_ims.get(im_name)
        manager_status = "🛑已停止"
        if im_manager:
            backend_port = im_manager.ports.get(6099)
            send_port = im_manager.ports.get(3001)
            if not im_manager.running:
                manager_status = "🛑已停止"
            elif im_manager.current_state == "LOGIN":
                manager_status = f"🟡运行中(未登录) | [点击登录](http://localhost:{backend_port}/webui?token=napcat)"
            elif im_manager.current_state == "ONLINE":
                manager_status = f"🟢运行中(已登录) | [访问后台](http://localhost:{backend_port}/webui?token=napcat)"
            else:
                manager_status = f"🔵状态: {im_manager.current_state}"
        else:
            logger.warning(
                f"Managed instance '{instance_name}' not found in WorkerManager."
            )
        schema["schema"]["properties"]["managed"]["description"] = manager_status

    if send_port:
        from amadeus.executors.im import InstantMessagingClient

        im = InstantMessagingClient(api_base=f"ws://localhost:{send_port}")
        logger.info(
            f"Enhancing schema for class '{class_name}' with instance '{instance_name}' using IM client on port {send_port}"
        )
        try:
            groups = await im.get_joined_groups()
            if groups:
                schema["schema"]["properties"]["enabled_groups"]["suggestions"] = [
                    {"title": group["group_name"], "const": str(group["group_id"])}
                    for group in groups
                ]
            return schema
        except Exception as e:
            import traceback
            logger.error(
                f"Error enhancing schema for class '{class_name}' with instance '{instance_name}': {str(e)}"
            )
            logger.error(
                traceback.format_exc()
            )

    return schema


async def model_list_enhancer(
    schema: Dict[str, Any],
    config_data: Dict[str, Any],
    class_name: str,
    instance_name: Optional[str] = None,
):
    """
    Enhance schema by resolving model lists.
    This is a placeholder for future implementation.
    """
    # Placeholder for model list logic
    logger.debug(
        f"Enhancing schema for class '{class_name}' with instance '{instance_name}'"
    )
    instances = config_data.get(class_name, [])
    for instance in instances:
        if instance.get("name") == instance_name:
            break
    else:
        return schema

    base_url = instance.get("base_url")
    if not base_url:
        return schema

    import httpx

    api_key = instance.get("api_key", "")

    try:
        headers = {
            "Authorization": f"Bearer {api_key}" if api_key else "",
            "Content-Type": "application/json",
        }
        models_url = f"{base_url}/models"

        async with httpx.AsyncClient() as client:
            response = await client.get(models_url, headers=headers)
            response.raise_for_status()
            models = response.json().get("data", [])
            models = [m for m in models if m.get("object") == "model"]
            if models:
                schema = copy.deepcopy(schema)
                if models:
                    schema["schema"]["properties"]["models"]["suggestions"] = [
                        {"title": model["id"], "const": model["id"]}
                        for model in models
                    ]
                return schema
    except Exception as e:
        logger.error(
            f"Error enhancing schema for class '{class_name}' with instance '{instance_name}': {str(e)}"
        )
        return schema

async def select_model_enhancer(
    schema: Dict[str, Any],
    config_data: Dict[str, Any],
    class_name: str,
    instance_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Enhance schema by resolving model selection.
    This is a placeholder for future implementation.
    """
    # Placeholder for model selection logic
    logger.debug(
        f"Enhancing schema for class '{class_name}' with instance '{instance_name}'"
    )
    instances = config_data.get(class_name, [])
    for instance in instances:
        if instance.get("name") == instance_name:
            break
    else:
        return schema

    schema = copy.deepcopy(schema)

    chat_model_provider = instance.get("chat_model_provider")
    if chat_model_provider:
        provider_instance = find_item(config_data, "model_providers", chat_model_provider)
        if provider_instance and provider_instance.get("models"):
            schema["schema"]["properties"]["chat_model"]["suggestions"] = provider_instance["models"]

    vision_model_provider = instance.get("vision_model_provider")
    if vision_model_provider:
        provider_instance = find_item(config_data, "model_providers", vision_model_provider)
        if provider_instance and provider_instance.get("models"):
            schema["schema"]["properties"]["vision_model"]["suggestions"] = provider_instance["models"]

    return schema


config_router.register_schema_enhancer(
    "apps",
    app_enhancer,
)
config_router.register_schema_enhancer(
    "model_providers",
    model_list_enhancer,
)
config_router.register_schema_enhancer(
    "characters",
    select_model_enhancer,
)
app.include_router(config_router.router)


def find_item(config_data, section_key, item_name):
    section_items = config_data.get(section_key, [])
    if not isinstance(section_items, list):
        return None  # Invalid section structure

    for item in section_items:
        if isinstance(item, dict) and item.get("name") == item_name:
            return item
    return None


def embed_config_item(current_item, config_data, section_key):
    """
    Embed references by replacing item names with actual item objects.
    Recursively resolves nested references.
    """
    if not isinstance(current_item, dict):
        return current_item

    section_schema_definition = (
        CONFIG_SCHEMA.get(section_key, {}).get("schema", {}).get("properties", {})
    )
    resolved_item = copy.deepcopy(current_item)

    for field_name, field_value in current_item.items():
        if field_name not in section_schema_definition:
            continue

        field_schema = section_schema_definition.get(field_name, {})

        def resolve_and_embed(value, source_section):
            referenced_item_object = find_item(config_data, source_section, value)
            if referenced_item_object:
                return embed_config_item(
                    referenced_item_object, config_data, source_section
                )
            raise fastapi.HTTPException(
                status_code=400,
                detail=f"Referenced item '{value}' in section '{source_section}' not found for field '{field_name}' in '{section_key}'.",
            )

        dynamic_enum_config = field_schema.get("$dynamicEnum")
        if dynamic_enum_config and isinstance(field_value, str):
            referenced_section_key = dynamic_enum_config.get("source")
            if referenced_section_key:
                resolved_item[field_name] = resolve_and_embed(
                    field_value, referenced_section_key
                )

        elif field_schema.get("type") == "array" and isinstance(field_value, list):
            items_schema = field_schema.get("items", {})
            dynamic_enum_config = items_schema.get("$dynamicEnum")
            if dynamic_enum_config:
                referenced_section_key = dynamic_enum_config.get("source")
                if referenced_section_key:
                    resolved_item[field_name] = [
                        resolve_and_embed(item, referenced_section_key)
                        if isinstance(item, str)
                        else item
                        for item in field_value
                    ]
    return resolved_item


def digest_config_data(config_data):
    seen_app_names = set()  # Track seen app names to avoid duplicates and log clearly
    resolved_apps = []
    apps_to_process = config_data.get("apps", [])
    logger.info(
        f"Digesting configuration: Found {len(apps_to_process)} app(s) defined in config."
    )

    for app_config in apps_to_process:
        app_name = app_config.get("name", "UnnamedApp")
        if app_name in seen_app_names:
            logger.warning(
                f"Duplicate app name '{app_name}' found in configuration, skipping subsequent instance."
            )
            continue
        seen_app_names.add(app_name)
        try:
            logger.info(
                f"Processing enabled app: '{app_name}'. Embedding referenced configurations."
            )
            embedded_app = embed_config_item(app_config, config_data, "apps")
            resolved_apps.append(embedded_app)
            logger.info(f"Successfully processed and resolved app: '{app_name}'.")
        except (
            fastapi.HTTPException
        ) as e:  # Assuming embed_config_item can raise HTTPException
            logger.error(
                f"Error processing app '{app_name}': {e.detail}. This app will be skipped."
            )
        except Exception as e:
            logger.error(
                f"Unexpected error processing app '{app_name}': {str(e)}. This app will be skipped."
            )

    logger.info(
        f"Digestion complete: {len(resolved_apps)} app(s) processed and enabled."
    )
    return resolved_apps


class WorkerManager:
    amadeus_workers: Dict[int, MultiprocessManager] = {}
    managed_ims: Dict[str, DockerRunManager] = {}
    watcher: Optional[asyncio.Task] = None

    @classmethod
    async def apply_config(cls, config_data):
        config_changed = False

        apps = digest_config_data(config_data)
        logger.info(f"Applying configuration. {len(apps)} app(s) to configure.")

        # Store app name along with hash for better logging
        im_info_map = {}
        for app_config in apps:
            if app_config.get("managed", False):
                name_hash = md5(app_config["name"].encode()).hexdigest()[:10]
                name = f"im-{name_hash}-{app_config['account']}"
                im_info_map[name] = {
                    "name": name,
                    "config": app_config,
                }

        prev_ims = set(cls.managed_ims.keys())
        current_ims = set(im_info_map.keys())

        to_remove_ims = prev_ims - current_ims
        to_add_ims = current_ims - prev_ims

        managed_ports = {}

        for im_key in to_remove_ims:
            if im_key in cls.managed_ims:
                manager = cls.managed_ims[im_key]
                await manager.close()
                del cls.managed_ims[im_key]

        if to_add_ims:
            for im_key in to_add_ims:
                config = im_info_map[im_key]["config"]
                manager = get_napcat_manager(
                    config_name=im_key,
                    account=config["account"],
                )
                await manager.start()
                cls.managed_ims[im_key] = manager
                try:
                    # Wait for a short period, then check the status
                    await asyncio.sleep(0.2)
                    while True:
                        await manager.state_changed.wait()
                        state = manager.current_state
                        if state in ["LOGIN", "ONLINE"] or not manager.running:
                            break

                    if not manager.running:
                        logger.error(
                            f"IM service for '{im_key}' failed to start or terminated prematurely. Current state: {state}"
                        )
                        await manager.close()
                        del cls.managed_ims[im_key]
                        return

                    for app in config_data.get("apps", []):
                        if app.get("name") == config["name"]:
                            app["send_port"] = manager.ports[3001]
                            break

                    logger.info(
                        f"IM service for '{im_key}' started successfully on port {manager.ports[3001]}."
                    )
                except Exception as e:
                    logger.error(f"Error during IM service startup for '{im_key}': {e}")
                    await manager.close()
                    if im_key in cls.managed_ims:
                        del cls.managed_ims[im_key]

        for im_key, manager in cls.managed_ims.items():
            config = im_info_map[im_key]["config"]
            managed_ports[config["name"]] = manager.ports[3001]

        app_info_map = {}
        apps = digest_config_data(config_data)
        for app_config in apps:
            if app_config.get("enable", False):
                if app_config.get("name") in managed_ports:
                    app_config["send_port"] = managed_ports[app_config["name"]]
                app_yaml = yaml.safe_dump(app_config, allow_unicode=True, sort_keys=True)
                app_hash = md5(app_yaml.encode()).hexdigest()[:10]
                name = f"amadeus-{app_hash}"
                app_info_map[name] = {
                    "name": name,
                    "config": app_config,
                }

        logger.info(
            f"IM services status: {len(cls.managed_ims)} IM service(s) now running."
        )

        prev_process_hashes = set(cls.amadeus_workers.keys())
        current_app_hashes = set(app_info_map.keys())
        logger.info(
            f"Current app hashes: {current_app_hashes}, Previous process hashes: {prev_process_hashes}"
        )

        to_remove_amadeus = prev_process_hashes - current_app_hashes
        to_add_amadeus = current_app_hashes - prev_process_hashes


        for app_hash in to_remove_amadeus:
            if app_hash in cls.amadeus_workers:
                manager = cls.amadeus_workers[app_hash]
                logger.info(f"Stopping service for app '{manager.name}'.")
                await manager.close()
                del cls.amadeus_workers[app_hash]

        if to_add_amadeus:
            for app_hash in to_add_amadeus:
                app_detail = app_info_map[app_hash]
                app_name = app_detail["name"]
                app_yaml = yaml.safe_dump(
                    app_detail["config"], allow_unicode=True, sort_keys=True
                )

                logger.info(f"Attempting to start service for app '{app_name}'.")

                manager = MultiprocessManager(
                    name=app_name,
                    target=run_amadeus_app_target,
                    args=(app_yaml, app_name),
                    stream_logs=True,
                )
                await manager.start()
                
                # 等待一小段时间，然后检查状态
                await asyncio.sleep(3)

                if manager.current_state != MultiprocessState.RUNNING:
                    logger.error(
                        f"Service for app '{app_name}' failed to start or terminated prematurely. Current state: {manager.current_state}"
                    )
                    await manager.close()
                    
                    config_changed = True
                    for app_config_item in config_data.get("apps", []):
                        if app_config_item.get("name") == app_name:
                            app_config_item["enable"] = False
                            logger.info(
                                f"App '{app_name}' has been disabled in the configuration due to startup failure."
                            )
                            break
                else:
                    logger.info(f"Service for app '{app_name}' started successfully.")
                    cls.amadeus_workers[app_hash] = manager


        logger.info(f"System status: {len(cls.amadeus_workers)} service(s) now running.")

        if cls.watcher is None or cls.watcher.done():
            cls.watcher = asyncio.create_task(cls.watch_processes())

        return config_data, config_changed

    @classmethod
    async def watch_processes(cls):
        """
        Monitors managed amadeus_workers and handles unexpected termination.
        """
        while True:
            try:
                for app_hash, manager in list(cls.amadeus_workers.items()):
                    if manager.current_state in [MultiprocessState.STOPPED, MultiprocessState.ERROR]:
                        logger.warning(
                            f"Service '{manager.name}' terminated unexpectedly with state: {manager.current_state}. "
                            "It will be removed from active amadeus_workers."
                        )
                        
                        await manager.close() # Ensure cleanup
                        del cls.amadeus_workers[app_hash]

                        logger.warning(
                            f"Automatic restart for '{manager.name}' is not yet implemented. The service will remain stopped."
                        )
                await asyncio.sleep(10)  # Check every 10 seconds
            except asyncio.CancelledError:
                logger.info("Process watcher task is shutting down.")
                break
            except Exception as e:
                logger.error(f"An error occurred in the process watcher: {e}")
                await asyncio.sleep(10) # wait before retrying


class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def setup_loguru():
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    )

    # 拦截标准 logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # 覆盖 uvicorn 和 fastapi 的 logger
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging.getLogger(name).handlers = [InterceptHandler()]
        logging.getLogger(name).propagate = False


if __name__ == "__main__":
    import uvicorn
    import os
    import socket
    import logging
    import sys

    def get_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            return s.getsockname()[1]

    # 确保在主进程中运行
    multiprocessing.freeze_support()  # Windows支持
    
    port = int(os.environ.get("PORT", get_free_port()))
    setup_loguru()

    if os.environ.get("DEV_MODE", "false").lower() == "true":
        uvicorn.run(
            "main:app",
            host="localhost",
            port=port,
            reload=False,  # 禁用reload，因为multiprocessing不兼容
            access_log=False,
            log_config=None,
            log_level="debug",
        )
    else:
        uvicorn.run(
            app,
            host="localhost",
            port=port,
            access_log=False,
            log_config=None,
            log_level="info",
        )
