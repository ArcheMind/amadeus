import time
import collections
import asyncio
import lmdb
import os
from amadeus.const import DATA_DIR
from amadeus.kvdb import KVModel
from amadeus.common import gray, green, blue, red, yellow
from amadeus.llm import llm
from amadeus.tools.context import ChatContext
from amadeus.config import AMADEUS_CONFIG
from loguru import logger


db_env = lmdb.open(os.path.join(DATA_DIR, "thinking.mdb"), map_size=100 * 1024**2)


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

                logger.info(
                    f"Processing event for target: {green(chat_type)} {blue(target_id)}"
                )

                # 使用ChatContext架构：user_loop -> context -> tools
                chat_context = ChatContext(
                    chat_type=chat_type,
                    target_id=target_id,
                    api_base=AMADEUS_CONFIG.onebot_server,
                )

                logger.trace(
                    f"Building chat context for {green(chat_type)} {blue(target_id)}"
                )
                messages = await chat_context.build_chat_messages(
                    last_view=state.last_view
                )
                state.last_view = state.next_view

                tools = chat_context.get_tools()

                logger.info(
                    f"Calling LLM for target: {green(chat_type)} {blue(target_id)} with {len(tools)} tools."
                )

                llm_output_chunks = []
                async for m in llm(
                    messages,
                    tools=tools,
                    continue_on_tool_call=False,
                    temperature=1,
                ):
                    logger.info(green(m))
                    logger.trace(
                        f"LLM output for {green(chat_type)} {blue(target_id)}: {yellow(str(m))}"
                    )
                    llm_output_chunks.append(str(m))

                if (
                    llm_output_chunks
                    and len(llm_output_chunks) > 0
                    and "yaml" in llm_output_chunks[0]
                ):
                    full_llm_output = "\n".join(llm_output_chunks)
                    thinking_db = KVModel(
                        db_env,
                        namespace=f"{chat_type}_{target_id}",
                        kind="thinking",
                    )
                    thinking_db.put("last_thought", full_llm_output)
                    logger.info(
                        f"Saved thinking process for target: {green(chat_type)} {blue(target_id)}"
                    )

                logger.info(
                    f"Finished processing for target: {green(chat_type)} {blue(target_id)}"
                )
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
        692484740,  # 新玩家群
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

DAEMONS = [user_loop]


from amadeus.executors.im import WsConnector


async def message_handler(data):
    """
    返回 True 表示消息被处理，False 表示消息被忽略
    """
    if data.get("post_type") != "message":
        return False

    # 检查中间件
    json_body = data
    middleware_blocked = False
    for middleware in MIDDLEWARES:
        if await middleware(json_body):
            middleware_blocked = True
            logger.trace(
                f"Message from {json_body.get('sender', {}).get('user_id')} blocked by {middleware.__name__}"
            )
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
        logger.trace(
            f"Debounce task scheduled for {green(target_type)} {blue(target_id)}"
        )
        TARGET_STATE[(target_type, target_id)].next_view = msg_time + DEBOUNCE_TIME
    return True


async def _main():
    # 故意制造一个错误来测试日志收集功能
    # raise RuntimeError("This is a test error to check logging functionality.")

    for daemon in DAEMONS:
        if daemon not in _TASKS:
            logger.info(f"Starting daemon: {green(daemon.__name__)}")
            _TASKS[daemon] = asyncio.create_task(daemon())

    uri = AMADEUS_CONFIG.onebot_server
    logger.info(f"Starting IM client connection to {green(uri)}")
    helper = WsConnector(uri)
    helper.register_event_handler(message_handler)

    # Start connection in background with retry logic
    async def connect_with_retry():
        retry_count = 0
        last_log_time = 0

        while True:
            try:
                current_time = asyncio.get_event_loop().time()

                # Log attempt only every 30 seconds to avoid spam
                if current_time - last_log_time >= 30:
                    logger.info(
                        f"Attempting to connect to IM client at {green(uri)} (attempt {retry_count + 1})"
                    )
                    last_log_time = current_time

                success = await helper.start()
                if success:
                    logger.info(
                        f"Successfully connected to IM client at {green(uri)} after {retry_count + 1} attempts"
                    )
                    try:
                        logger.info(
                            f"Waiting for WebSocket connection to {green(uri)} to close..."
                        )
                        await helper.join()
                        logger.warning(
                            f"WebSocket connection to {green(uri)} closed unexpectedly, will retry"
                        )
                    except Exception as e:
                        logger.error(f"Error in WebSocket connection: {e}")
                    # Don't break here, continue retrying
                else:
                    retry_count += 1
                    wait_time = min(
                        15, 5 + retry_count * 2
                    )  # Start at 5s, increase by 2s each time, cap at 15s
                    logger.info(
                        f"Connection failed, waiting {wait_time}s before retry {retry_count}"
                    )
                    await asyncio.sleep(wait_time)
            except Exception as e:
                retry_count += 1
                current_time = asyncio.get_event_loop().time()

                # Log error only every 60 seconds to avoid spam
                if current_time - last_log_time >= 60:
                    logger.error(f"Error connecting to IM client at {green(uri)}: {e}")
                    last_log_time = current_time

                wait_time = min(
                    15, 5 + retry_count * 2
                )  # Start at 5s, increase by 2s each time, cap at 15s
                logger.info(
                    f"Connection error, waiting {wait_time}s before retry {retry_count}"
                )
                await asyncio.sleep(wait_time)

    # Start the connection task
    connection_task = asyncio.create_task(connect_with_retry())

    # Wait for all tasks to complete (including daemons and connection)
    logger.info(
        f"Waiting for {len(_TASKS)} daemon task(s) and connection task to complete..."
    )
    try:
        all_tasks = list(_TASKS.values()) + [connection_task]
        results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # Log any exceptions that occurred
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                task_name = (
                    list(_TASKS.keys())[i] if i < len(_TASKS) else "connection_task"
                )
                logger.error(f"Task {task_name} failed with exception: {result}")

    except Exception as e:
        logger.error(f"Error in main loop: {e}")
        raise


def main():
    asyncio.run(_main())
