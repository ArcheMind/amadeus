import os
import sys
from loguru import logger
import multiprocessing

def run_amadeus_app_target(config_yaml: str, app_name: str):
    """
    在子进程中运行amadeus app的函数
    """
    # 设置环境变量
    os.environ["AMADEUS_CONFIG"] = config_yaml
    os.environ["AMADEUS_APP_NAME"] = app_name

    multiprocessing.freeze_support()
    
    # 在子进程中重新配置loguru，确保日志通过stdout发送到父进程的队列中
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        enqueue=True,  # 使用队列来确保进程安全
        backtrace=True, # 记录异常回溯
        colorize=True,
    )
    
    # 启动amadeus app
    from amadeus.app import main

    try:
        main()
    except Exception:
        # 抛出异常以确保子进程以错误代码退出
        raise
