import time
import collections
import asyncio
from amadeus.common import gray, green
from amadeus.llm import llm
from amadeus.tools.im import QQChat
from amadeus.config import AMADEUS_CONFIG
from loguru import logger



TARGETS = set()
DEBOUNCE_TIME = 1.5


class State:
    def __init__(self, last_view: int = 0):
        self.last_view = last_view
        self.next_view = 0


TARGET_STATE = collections.defaultdict(lambda: State())



async def user_loop():
    logger.info("主机器人循环启动")
    while True:
        try:
            await asyncio.sleep(1)
            for (chat_type, target_id), state in TARGET_STATE.items():
                if state.last_view >= state.next_view:
                    continue
                if state.next_view >= time.time():
                    continue
                qq_chat = QQChat(
                    api_base=f"ws://localhost:{AMADEUS_CONFIG.send_port}",
                    chat_type=chat_type,
                    target_id=target_id,
                )
                content = await qq_chat.view_chat_context()
                state.last_view = state.next_view

                TOOL_MAP = {
                    "撤回消息": qq_chat.delete_message,
                    "群管理-禁言": qq_chat.set_group_ban,
                }

                tools = [
                    TOOL_MAP[t]
                    for t in AMADEUS_CONFIG.enabled_tools
                    if t in TOOL_MAP
                ]
                
                async for m in llm(
                    [
                        {
                            "role": "user",
                            "content": content,
                        }
                    ],
                    tools=[
                        qq_chat.send_message,
                        qq_chat.ignore,
                    ] + tools,
                    continue_on_tool_call=False,
                    temperature=1,
                ):
                    logger.info(green(m))
        except Exception as e:
            logger.error(f"主机器人循环异常: {e}")



USER_BLACKLIST = set(
    [
        3288903870,
        3694691673,
        3877042072,
        3853260942,
        3858404312,
        2224638710,
    ]
)


async def user_blacklist(json_body):
    if json_body.get("sender", {}).get("user_id") in USER_BLACKLIST:
        return True


TARGET_WHITELIST = set(
    [
        119851258,
        # 829109637,  # 卅酱群
        # 608533421,  # key
        980036058,  # 小琪
        # 773891671,  # 暗CLub
        904631854,  # tama木花
        697273289,  # 开放墨蓝
        # 959357003,  # 鸥群
        692484740,    # 新玩家群
    ]
)


async def group_whitelist(json_body):
    if str(json_body.get("group_id")) not in AMADEUS_CONFIG.enabled_groups:
        return True



MIDDLEWARES = [
    group_whitelist,
    user_blacklist,
]


_TASKS = {}

DAEMONS = [
    user_loop
]



from amadeus.executors.im import WsConnector


async def message_handler(data):
    '''
    返回 True 表示消息被处理，False 表示消息被忽略
    '''
    if data.get("post_type") != "message":
        return False
    
    # 检查中间件
    json_body = data
    middleware_blocked = False
    for middleware in MIDDLEWARES:
        if await middleware(json_body):
            middleware_blocked = True
            break
    
    if middleware_blocked:
        return True

    if json_body.get("group_id"):
        target_type = "group"
    else:
        target_type = "private"
    target_id = json_body.get("group_id", 0) or json_body.get("user_id", 0)
    msg_time = json_body.get("time", 0)
    if msg_time:
        logger.info(
            gray(
                f"收到消息: {target_type} {target_id} at {msg_time}, "
                f"内容: {json_body.get('message', '')}"
            )
        )
        TARGET_STATE[(target_type, target_id)].next_view = msg_time + DEBOUNCE_TIME
    return True


async def _main():
    for daemon in DAEMONS:
        if daemon not in _TASKS:
            _TASKS[daemon] = asyncio.create_task(daemon())
    logger.info(green(f"主进程启动， 连接到 WebSocket 端口 {AMADEUS_CONFIG.send_port}"))
    port = AMADEUS_CONFIG.send_port
    uri = f"ws://localhost:{port}/"
    helper = WsConnector(uri)
    helper.register_event_handler(message_handler)
    await helper.start()
    

def main():
    asyncio.run(_main())

