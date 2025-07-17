# This is a full file replacement to fix previous editing errors.
import os
import platform
import subprocess
from hashlib import md5
import logging
from loguru import logger
import sys


# Windows 编码问题修复：只在直接运行时设置（npm run dev 时由 run.js 设置）
if platform.system() == "Windows" and not os.environ.get('PYTHONIOENCODING'):
    # 设置环境变量，优先级最高
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONLEGACYWINDOWSSTDIO'] = '1'
    
    # 重新配置标准输出流
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional

import multiprocessing
import copy
import fastapi
import socket
import asyncio
from amadeus.common import green, blue, red, yellow, APP_VERSION
from amadeus.config_schema import CONFIG_SCHEMA, EXAMPLE_CONFIG
from amadeus.config_router import ConfigRouter
from amadeus.config_persistence import ConfigPersistence
from amadeus.observers import AmadeusObserver, IMObserver
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def lifespan(_: fastapi.FastAPI):
    """
    Lifespan event handler for FastAPI to manage startup and shutdown events.
    """
    try:
        # Startup event
        logger.info(f"{yellow('--- Application starting up ---')}")
        config_data = config_persistence.load()
        for app in config_data.get("apps", []):
            if not isinstance(app, dict):
                continue
            if app.get("enable", False):
                app["enable"] = False
            if app.get("managed", False):
                app["managed"] = False
        config_persistence.save(config_data)
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
    version=APP_VERSION,
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
    import traceback
    import asyncio
    
    # Get call stack information
    stack = traceback.extract_stack()
    caller_info = f"{stack[-2].filename}:{stack[-2].lineno} in {stack[-2].name}"
    logger.info(f"{yellow('--- User triggered configuration save ---')} (called from: {caller_info})")
    
    original_config = copy.deepcopy(config_data)
    modified_config_data = await WorkerManager.apply_config(config_data)
    config_persistence.save(modified_config_data)
    logger.info(f"{yellow('--- Configuration saved and applied ---')}")
    return original_config != modified_config_data


def check_docker_status():
    """
    Check if Docker is running on both Windows and POSIX systems.
    Returns tuple (is_running: bool, status_message: str)
    """
    try:
        system = platform.system().lower()
        
        if system == "windows":
            # For Windows, check if Docker Desktop is running
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=5
            )
        else:
            # For POSIX systems (Linux, macOS), check docker daemon
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=5
            )
        
        if result.returncode == 0:
            return True, "🟢Docker运行中"
        else:
            return False, "🔴Docker未运行"
            
    except subprocess.TimeoutExpired:
        return False, "🔴Docker检测超时"
    except FileNotFoundError:
        return False, "🔴Docker未安装"
    except Exception as e:
        return False, f"🔴Docker检测失败: {str(e)}"


async def check_napcat_login_status(backend_server: str) -> str:
    """
    Check NapCat login status through HTTP API.
    Returns "ONLINE", "LOGIN", "starting", or "error"
    """
    if not backend_server:
        return "error"
    
    import aiohttp
    
    try:
        timeout = aiohttp.ClientTimeout(total=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Get auth token
            auth_payload = {"hash": "fab552ce31e45b51288bb374b7e08d720f1d612e20fb7361246139c1e476f0b0"}
            async with session.post(f"{backend_server}/api/auth/login", json=auth_payload) as response:
                if response.status != 200:
                    return "starting"
                
                data = await response.json()
                if data.get("code") != 0:
                    return "starting"
                
                token = data.get("data", {}).get("Credential")
                if not token:
                    return "starting"
            
            # Check login status
            headers = {"Authorization": f"Bearer {token}"}
            async with session.post(f"{backend_server}/api/QQLogin/CheckLoginStatus", headers=headers) as response:
                if response.status != 200:
                    return "starting"
                
                data = await response.json()
                if data.get("code") != 0:
                    return "starting"
                
                login_data = data.get("data", {})
                if login_data.get("isLogin", False):
                    return "ONLINE"
                elif login_data.get("qrcodeurl"):
                    return "LOGIN"
                else:
                    return "starting"
                    
    except Exception as e:
        logger.debug(f"Exception checking login status for {backend_server}: {e}")
        return "error"


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

    # Get account type and server info
    account_type = instance.get("type", "NapCatQQ")
    onebot_server = instance.get("onebot_server", "")
    backend_server = instance.get("_backend_server", "")

    # Control field visibility based on account type
    if account_type == "NapCatQQ":
        # Hide onebot_server field for NapCatQQ type
        schema["schema"]["properties"]["onebot_server"]["hidden"] = True
        schema["schema"]["properties"]["onebot_server"]["readOnly"] = True
        
        # Show managed field and handle its status
        if instance.get("managed", False):
            manager_status = "🛑已停止"
            
            if backend_server:
                # Check login status using the independent function
                logger.info(f"Enhancer: Checking login status for {blue(instance_name)}")
                try:
                    connection_status = await check_napcat_login_status(backend_server)
                    logger.info(f"Enhancer: Login status for {blue(instance_name)}: {connection_status}")
                    
                    if connection_status == "LOGIN":
                        manager_status = f"🟡运行中(未登录) | [点击登录]({backend_server}/webui?token=napcat)"
                    elif connection_status == "ONLINE":
                        manager_status = f"🟢运行中(已登录) | [访问后台]({backend_server}/webui?token=napcat)"
                    elif connection_status == "starting":
                        manager_status = f"🔵正在启动... | [访问后台]({backend_server}/webui?token=napcat)"
                    else:
                        manager_status = f"🔵状态: {connection_status}"
                except Exception as e:
                    logger.warning(f"Enhancer: Failed to check login status for {blue(instance_name)}: {e}")
                    manager_status = f"🔵状态检查失败 | [访问后台]({backend_server}/webui?token=napcat)"
            # Add Docker status to managed description
            schema["schema"]["properties"]["managed"]["description"] = manager_status
        else:
            docker_running, docker_status = check_docker_status()
            # If not managed, still show Docker status and make managed readOnly if Docker is not running
            if not docker_running:
                schema["schema"]["properties"]["managed"]["readOnly"] = True
            
            # Add Docker status to managed description
            current_description = schema["schema"]["properties"]["managed"].get("description", "")
            if current_description:
                schema["schema"]["properties"]["managed"]["description"] = f"{current_description} | {docker_status}"
            else:
                schema["schema"]["properties"]["managed"]["description"] = docker_status
    else:
        # account_type == "自定义"
        # Hide managed field for custom type
        schema["schema"]["properties"]["managed"]["hidden"] = True
        schema["schema"]["properties"]["managed"]["readOnly"] = True
        schema["schema"]["properties"]["account"]["readOnly"] = True
        schema["schema"]["properties"]["account"]["hidden"] = True
        
        # Show onebot_server field and handle its status
        if onebot_server:
            logger.debug(f"Enhancer: Found onebot_server for app {blue(instance_name)}: {green(onebot_server)}")
            
            from amadeus.executors.im import WsConnector

            logger.info(f"Enhancer: Connecting to Onebot server at {green(onebot_server)} for app {blue(instance_name)}")
            connector = WsConnector(onebot_server)
            if await connector.start():
                schema["schema"]["properties"]["onebot_server"]["description"] = f"🟢已连接"
            else:
                schema["schema"]["properties"]["onebot_server"]["description"] = f"🔴连接失败"

    # Handle group suggestions for both types
    if onebot_server:
        from amadeus.executors.im import InstantMessagingClient

        im = InstantMessagingClient(api_base=onebot_server)
        try:
            logger.info(f"Enhancer: Fetching joined groups for app {blue(instance_name)} from {green(onebot_server)}")
            
            # Check real-time connection status if managed
            if account_type == "NapCatQQ" and instance.get("managed", False) and backend_server:
                try:
                    connection_status = await check_napcat_login_status(backend_server)
                    logger.debug(f"Enhancer: Real-time connection status for app {blue(instance_name)}: {connection_status}")
                    
                    if connection_status != "ONLINE":
                        logger.warning(f"Enhancer: App {blue(instance_name)} is not online (status: {connection_status}), but trying to get groups anyway")
                except Exception as e:
                    logger.warning(f"Enhancer: Failed to check real-time status for app {blue(instance_name)}: {e}")
            else:
                logger.debug(f"Enhancer: Non-managed app {blue(instance_name)}, will test WebSocket connection directly")
            
            # Try to get groups
            groups = await im.get_joined_groups()
            if groups:
                logger.info(f"Enhancer: Successfully fetched {green(len(groups))} groups for app {blue(instance_name)}.")
                schema["schema"]["properties"]["enabled_groups"]["suggestions"] = [
                    {"title": group["group_name"], "const": str(group["group_id"])}
                    for group in groups
                ]
            else:
                logger.warning(f"Enhancer: No groups returned for app {blue(instance_name)} from {green(onebot_server)}")
            return schema
        except Exception as e:
            logger.warning(f"Enhancer: Failed to fetch joined groups for app {blue(instance_name)} from {green(onebot_server)}: {red(str(e))}")
            logger.debug(f"Enhancer: Error details for app {blue(instance_name)}: {e.__class__.__name__}: {e}")
            pass
    else:
        logger.debug(f"Enhancer: No onebot_server found for app {blue(instance_name)}")
        
        # Show real-time status if managed
        if account_type == "NapCatQQ" and instance.get("managed", False) and backend_server:
            try:
                connection_status = await check_napcat_login_status(backend_server)
                logger.debug(f"Enhancer: Instance data: managed={instance.get('managed', False)}, real-time_connection_status={connection_status}")
            except Exception as e:
                logger.debug(f"Enhancer: Instance data: managed={instance.get('managed', False)}, status_check_failed={e}")
        else:
            logger.debug(f"Enhancer: Instance data: managed={instance.get('managed', False)}, non_managed_or_no_container")

    return schema


async def model_provider_enhancer(
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


async def character_enhancer(
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
    model_provider_enhancer,
)
config_router.register_schema_enhancer(
    "characters",
    character_enhancer,
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
        
        for i, observer in enumerate(cls.observers):
            observer_name = observer.__class__.__name__
            logger.debug(f"Processing observer {i+1}/{len(cls.observers)}: {observer_name}")
            
            config_data_before = len(config_data.get("apps", []))
            config_data = await observer.update(config_data)
            config_data_after = len(config_data.get("apps", []))
            
            logger.debug(f"{observer_name} processed: {config_data_before} -> {config_data_after} apps")

        logger.info(f"{yellow('--- Configuration application finished ---')}")
        return config_data


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
    multiprocessing.freeze_support()
    # Windows平台特殊处理：设置事件循环策略
    if platform.system() == "Windows":
        # 在Windows上，multiprocessing的spawn模式需要特殊处理asyncio
        # 设置事件循环策略以避免卡死
        if sys.version_info >= (3, 8):
            # Python 3.8+使用WindowsProactorEventLoopPolicy
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        else:
            # Python 3.7及以下使用WindowsSelectorEventLoopPolicy
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # 设置multiprocessing使用spawn方法
    # 必须在 "if __name__ == '__main__':" 块中调用
    multiprocessing.set_start_method('spawn', force=True)
    import uvicorn
    setup_loguru()

    port = os.getenv("PORT", "")
    if not port.isdigit():
        port = get_free_port()
    else:
        port = int(port)
    logger.info(f"Starting Amadeus v{APP_VERSION} server at http://localhost:{port}")
    uvicorn.run(
        app, host="0.0.0.0", port=port,
        access_log=False,
    ) 
