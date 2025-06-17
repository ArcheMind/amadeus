# This is a full file replacement to fix previous editing errors.
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
from amadeus.common import green, blue, red, yellow
from amadeus.config_schema import CONFIG_SCHEMA, EXAMPLE_CONFIG
from amadeus.config_router import ConfigRouter
from amadeus.config_persistence import ConfigPersistence
from amadeus.observers import AmadeusObserver, IMObserver
import yaml
from fastapi.middleware.cors import CORSMiddleware


# 设置multiprocessing使用spawn方法
multiprocessing.set_start_method('spawn', force=True)


@asynccontextmanager
async def lifespan(_: fastapi.FastAPI):
    """
    Lifespan event handler for FastAPI to manage startup and shutdown events.
    """
    try:
        # Startup event
        logger.info(f"{yellow('--- Application starting up ---')}")
        config_data = config_persistence.load()
        logger.info("Applying initial configuration...")
        config_data, _ = await WorkerManager.apply_config(config_data)
        logger.info(f"{yellow('--- Application startup complete ---')}")
        yield  # Yield control back to the FastAPI app
    finally:
        # Shutdown event
        logger.info(f"{yellow('--- Application shutting down ---')}")
        for observer in WorkerManager.observers:
            await observer.close()
        logger.info(f"{yellow('--- Application shutdown complete ---')}")


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
    logger.info(f"{yellow('--- User triggered configuration save ---')}")
    modified_config_data, config_changed = await WorkerManager.apply_config(config_data)
    config_persistence.save(modified_config_data)
    logger.info(f"{yellow('--- Configuration saved and applied ---')}")
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
    if not instance_name:
        return schema

    instance = find_item(config_data, class_name, instance_name)
    if not instance:
        return schema

    schema = copy.deepcopy(schema)

    onebot_server = instance.get("onebot_server", "")

    if instance.get("managed", False):
        im_name = f"im-{md5(instance_name.encode()).hexdigest()[:10]}-{instance.get('account', 'default')}"
        im_manager = None
        for observer in WorkerManager.observers:
            if isinstance(observer, IMObserver):
                im_manager = observer.managed_ims.get(im_name)
                break
        manager_status = "🛑已停止"
        if im_manager:
            backend_port = im_manager.ports.get(6099)
            if 3001 in im_manager.ports:
                send_port = im_manager.ports.get(3001)
                onebot_server = f"ws://localhost:{send_port}"
            if not im_manager.running:
                manager_status = "🛑已停止"
            elif im_manager.current_state == "LOGIN":
                manager_status = f"🟡运行中(未登录) | [点击登录](http://localhost:{backend_port}/webui?token=napcat)"
            elif im_manager.current_state == "ONLINE":
                manager_status = f"🟢运行中(已登录) | [访问后台](http://localhost:{backend_port}/webui?token=napcat)"
            else:
                manager_status = f"🔵状态: {im_manager.current_state}"
        else:
            pass
        schema["schema"]["properties"]["onebot_server"]["readOnly"] = True
        schema["schema"]["properties"]["onebot_server"]["hidden"] = True
        schema["schema"]["properties"]["managed"]["description"] = manager_status

    if onebot_server:
        if not instance.get("managed", False):
            from amadeus.executors.im import WsConnector

            logger.info(f"Enhancer: Connecting to Onebot server at {green(onebot_server)} for app {blue(instance_name)}")
            connector = WsConnector(onebot_server)
            if await connector.start():
                schema["schema"]["properties"]["onebot_server"]["description"] = f"🟢已连接"
            else:
                schema["schema"]["properties"]["onebot_server"]["description"] = f"🔴连接失败"

        from amadeus.executors.im import InstantMessagingClient

        im = InstantMessagingClient(api_base=onebot_server)
        try:
            logger.info(f"Enhancer: Fetching joined groups for app {blue(instance_name)} from {green(onebot_server)}")
            groups = await im.get_joined_groups()
            if groups:
                logger.info(f"Enhancer: Successfully fetched {green(len(groups))} groups for app {blue(instance_name)}.")
                schema["schema"]["properties"]["enabled_groups"]["suggestions"] = [
                    {"title": group["group_name"], "const": str(group["group_id"])}
                    for group in groups
                ]
            return schema
        except Exception as e:
            logger.error(f"Enhancer: Failed to fetch joined groups for app {blue(instance_name)} from {green(onebot_server)}: {red(str(e))}")
            pass

    return schema


async def model_list_enhancer(
    schema: Dict[str, Any],
    config_data: Dict[str, Any],
    class_name: str,
    instance_name: Optional[str] = None,
):
    instance = find_item(config_data, class_name, instance_name)
    if not instance:
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

        logger.info(f"Enhancer: Fetching models for provider {blue(instance_name)} from {green(models_url)}")
        masked_headers = headers.copy()
        if "Authorization" in masked_headers and masked_headers["Authorization"]:
            masked_headers["Authorization"] = "Bearer ***"
        logger.trace(f"Requesting {blue(models_url)} with headers: {yellow(str(masked_headers))}")

        async with httpx.AsyncClient() as client:
            response = await client.get(models_url, headers=headers)

        if response.status_code == 200:
            models_data = response.json()
            model_ids = [model["id"] for model in models_data.get("data", [])]
            if model_ids:
                schema["schema"]["properties"]["models"]["suggestions"] = model_ids
                logger.info(f"Enhancer: Successfully fetched {green(len(model_ids))} models for provider {blue(instance_name)}.")
            else:
                logger.warning(f"Enhancer: No models found for provider {blue(instance_name)}.")
        else:
            logger.error(
                f"Enhancer: Failed to fetch models for provider {blue(instance_name)}: {response.status_code} {response.text}"
            )

    except Exception as e:
        logger.error(f"Enhancer: Error fetching models for provider {blue(instance_name)}: {e}")
        
    return schema


async def select_model_enhancer(
    schema: Dict[str, Any],
    config_data: Dict[str, Any],
    class_name: str,
    instance_name: Optional[str] = None,
) -> Dict[str, Any]:
    providers = config_data.get("model_providers", [])
    all_models = []
    for provider in providers:
        all_models.extend(provider.get("models", []))
    
    if all_models:
        schema["schema"]["properties"]["chat_model"]["suggestions"] = all_models
        schema["schema"]["properties"]["vision_model"]["suggestions"] = all_models
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
        return None

    for item in section_items:
        if isinstance(item, dict) and item.get("name") == item_name:
            return item
    return None

class WorkerManager:
    observers: list = [IMObserver(), AmadeusObserver()]

    @classmethod
    async def apply_config(cls, config_data):
        logger.info(f"{yellow('--- Applying configuration ---')}")
        
        config_changed = False
        original_config = copy.deepcopy(config_data)
        
        for observer in cls.observers:
            config_data = await observer.update(config_data)
            
        if original_config != config_data:
            config_changed = True

        logger.info(f"{yellow('--- Configuration application finished ---')}")
        return config_data, config_changed


class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        
        frame = sys._getframe(2)
        depth = 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_loguru():
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

if __name__ == "__main__":
    import uvicorn
    setup_loguru()

    port = os.getenv("PORT", "")
    if not port.isdigit():
        port = get_free_port()
    else:
        port = int(port)
    logger.info(f"Starting Amadeus server at http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port) 
