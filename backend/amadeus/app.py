import time
import collections
import asyncio
from amadeus.common import gray, green, blue, red, yellow
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
    while True:
        try:
            await asyncio.sleep(1)
            for (chat_type, target_id), state in list(TARGET_STATE.items()):
                if state.last_view >= state.next_view:
                    continue
                if state.next_view >= time.time():
                    continue
                
                logger.info(f"Processing event for target: {green(chat_type)} {blue(target_id)}")

                qq_chat = QQChat(
                    api_base=AMADEUS_CONFIG.onebot_server,
                    chat_type=chat_type,
                    target_id=target_id,
                )
                logger.trace(f"Viewing chat context for {green(chat_type)} {blue(target_id)}")
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
                
                logger.info(f"Calling LLM for target: {green(chat_type)} {blue(target_id)} with {len(tools) + 2} tools.")
                logger.trace(f"LLM input content:\n{content}")

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
                    logger.trace(f"LLM output for {green(chat_type)} {blue(target_id)}: {yellow(str(m))}")
                    pass
                logger.info(f"Finished processing for target: {green(chat_type)} {blue(target_id)}")
        except Exception as e:
            logger.error(f"Error in user_loop: {red(str(e))}")
            pass



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
            logger.trace(f"Message from {json_body.get('sender', {}).get('user_id')} blocked by {middleware.__name__}")
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
        logger.trace(f"Debounce task scheduled for {green(target_type)} {blue(target_id)}")
        TARGET_STATE[(target_type, target_id)].next_view = msg_time + DEBOUNCE_TIME
    return True


async def _main():
    for daemon in DAEMONS:
        if daemon not in _TASKS:
            logger.info(f"Starting daemon: {green(daemon.__name__)}")
            _TASKS[daemon] = asyncio.create_task(daemon())
    uri = AMADEUS_CONFIG.onebot_server
    logger.info(f"Connecting to IM client at {green(uri)}")
    helper = WsConnector(uri)
    helper.register_event_handler(message_handler)
    await helper.start()
    await helper.join()
    

def main():
    asyncio.run(_main())

