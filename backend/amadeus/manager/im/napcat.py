import os
import sys

from amadeus.manager.docker_manager import DockerRunManager
from amadeus.common import find_free_ports
from amadeus.const import CACHE_DIR



def get_napcat_manager(config_name: str, account: str, app_name: str = None) -> DockerRunManager:
    instance_path = CACHE_DIR / "im" / f"{config_name}"
    napcat_config_path = instance_path / "napcat" / "config"
    ntqq_path = instance_path / "ntqq"
    napcat_cache_path = instance_path / "napcat" / "cache"

    napcat_config_path.mkdir(parents=True, exist_ok=True)
    ntqq_path.mkdir(parents=True, exist_ok=True)
    napcat_cache_path.mkdir(parents=True, exist_ok=True)

    api_port, webui_port = find_free_ports(2)

    env_vars = {
        "MODE": "ws",
        "ACCOUNT": account,
    }
    if sys.platform != "win32":
        env_vars["NAPCAT_UID"] = str(os.getuid())
        env_vars["NAPCAT_GID"] = str(os.getgid())

    custom_states = {
        "LOGIN": r"请扫描下面的二维码，然后在手Q上授权登录：",
        "ONLINE": r"\[Notice\] \[OneBot11\] \[network\] 配置加载",
    }

    # Add labels for configuration tracking
    labels = {
        "amadeus.app.name": app_name or config_name,
        "amadeus.app.account": account,
        "amadeus.app.type": "im",
        "amadeus.manager.type": "napcat",
    }

    manager = DockerRunManager(
        image_name="mlikiowa/napcat-docker:latest",
        name=config_name,
        custom_states=custom_states,
        ports={api_port: 3001, webui_port: 6099},
        volumes={
            str(napcat_config_path.resolve()): "/app/napcat/config",
            str(ntqq_path.resolve()): "/app/.config/QQ",
        },
        env=env_vars,
        label=labels
    )
    return manager
