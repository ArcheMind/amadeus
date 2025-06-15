import asyncio
import hashlib
import os
import sys

from amadeus.manager.docker_manager import DockerRunManager
from amadeus.common import find_free_ports
from amadeus.const import CACHE_DIR



async def get_napcat_manager(config_name: str, account: str) -> DockerRunManager:
    config_hash = hashlib.sha256(config_name.encode()).hexdigest()[:10]
    instance_path = CACHE_DIR / "im" / f"{config_hash}-{account}"
    
    napcat_config_path = instance_path / "napcat" / "config"
    ntqq_path = instance_path / "ntqq"
    napcat_cache_path = instance_path / "napcat" / "cache"

    napcat_config_path.mkdir(parents=True, exist_ok=True)
    ntqq_path.mkdir(parents=True, exist_ok=True)
    napcat_cache_path.mkdir(parents=True, exist_ok=True)

    api_port, webui_port = find_free_ports(2)

    container_name = f"amadeus-napcat-{account}"
    
    env_vars = {
        "MODE": "ws",
        "ACCOUNT": account,
    }
    if sys.platform != "win32":
        env_vars["NAPCAT_UID"] = str(os.getuid())
        env_vars["NAPCAT_GID"] = str(os.getgid())

    custom_states = {
        "WAITING_FOR_QR_SCAN": r"请扫描下面的二维码，然后在手Q上授权登录：",
        "CLIENT_STARTED": r"\[Notice\] \[OneBot11\] \[network\] 配置加载",
    }

    manager = DockerRunManager(
        image_name="mlikiowa/napcat-docker:latest",
        container_name=container_name,
        custom_states=custom_states,
        ports={api_port: 3000, webui_port: 6099},
        volumes={
            str(napcat_config_path.resolve()): "/app/napcat/config",
            str(ntqq_path.resolve()): "/app/.config/QQ",
        },
        env=env_vars
    )
    return manager

    # await manager.start()
    #
    # scan_ready = await manager.wait_for_state("WAITING_FOR_QR_SCAN", timeout=120)
    #
    # if not scan_ready:
    #     await manager.stop()
    #     return
    #
    # client_started = await manager.wait_for_state("CLIENT_STARTED", timeout=120)
    # 
    # if not client_started:
    #     pass
    # 
    # try:
    #     await manager.wait_for_state(DockerContainerState.STOPPED)
    # except asyncio.CancelledError:
    #     pass
    # finally:
    #     await manager.close()


# async def main():
#     await run_napcat_instance(config_name="my-napcat-config", account="3859568962")
#
# if __name__ == "__main__":
#     try:
#         asyncio.run(main())
#     except KeyboardInterrupt:
#         pass 
