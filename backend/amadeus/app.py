import time
from amadeus.tools.context import set_tool_context
import uuid
import collections
import asyncio
import lmdb
import os
from amadeus.const import DATA_DIR
from amadeus.kvdb import KVModel
from amadeus.common import gray, green, blue, red, yellow
from amadeus.llm import llm
from amadeus.context import ChatContext
from amadeus.config import AMADEUS_CONFIG
from loguru import logger
from amadeus.tools.im import QQChat
from amadeus.executors.im import InstantMessagingClient, WsConnector

# This global db_env is now only for 'thinking' and other potential global stores, not messages.
db_env = lmdb.open(os.path.join(DATA_DIR, "store.mdb"), map_size=100 * 1024**2)


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

                chat_context = ChatContext(
                    chat_type=chat_type,
                    target_id=target_id,
                    api_base=AMADEUS_CONFIG.onebot_server,
                )
                
                # Ensure the QQChat instance and bot info are loaded before proceeding
                await chat_context.ensure_qq_chat_instance()

                logger.trace(
                    f"Building chat context for {green(chat_type)} {blue(target_id)}"
                )
                messages = await chat_context.build_chat_messages(
                    last_view=state.last_view
                )
                state.last_view = state.next_view

                # get_tools is now a sync method, call it after ensuring instance exists
                tools = chat_context.get_tools()

                logger.info(
                    f"Calling LLM for target: {green(chat_type)} {blue(target_id)} with {len(tools)} tools."
                )

                llm_output_chunks = []
                with set_tool_context(
                    dict(last_view=state.last_view, chat_context=chat_context)
                ):
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
            logger.exception(e)


async def user_blacklist(json_body):
    if json_body.get("sender", {}).get("user_id") in USER_BLACKLIST:
        return True


async def group_whitelist(json_body):
    if (
        not json_body.get("group_id")
        and str(json_body.get("group_id")) not in AMADEUS_CONFIG.enabled_groups
    ):
        return True


async def post_type_whitelist(json_body):
    if json_body.get("post_type") not in ["message", "notice"]:
        return True


async def group_whitelist(json_body):
    if json_body.get("group_id") is None:
        return False
    if str(json_body.get("group_id")) not in AMADEUS_CONFIG.enabled_groups:
        return True


MIDDLEWARES = [
    post_type_whitelist,
    group_whitelist,
    # user_blacklist,
]


_TASKS = {}
_SELF_ID = None

DAEMONS = [user_loop]


async def get_self_id():
    """Get and cache the bot's self_id."""
    global _SELF_ID
    if _SELF_ID is None:
        client = InstantMessagingClient(AMADEUS_CONFIG.onebot_server)
        try:
            info = await client.get_login_info()
            _SELF_ID = info["user_id"]
            logger.info(f"Successfully retrieved self_id: {blue(_SELF_ID)}")
        except Exception as e:
            logger.error(f"Failed to get self_id: {e}. Will retry.")
            _SELF_ID = None # Reset on failure to allow retry
    return _SELF_ID


async def message_handler(data):
    """
    Returns True if the message is processed, False otherwise.
    """
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
        target_id = json_body.get("group_id")
    else:
        target_type = "private"
        target_id = json_body.get("user_id")

    if not target_id:
        logger.warning(f"Could not determine target_id for message: {json_body}")
        return False

    message_id = json_body.get("message_id")
    if not message_id:
        message_id = int.from_bytes(uuid.uuid4().bytes[:4], byteorder="big")
        json_body["message_id"] = message_id

    # Get self_id to correctly instantiate QQChat
    self_id = await get_self_id()
    if not self_id:
        logger.error("Could not get self_id, unable to save message.")
        return False

    # Instantiate QQChat to handle its own database
    qq_chat_instance = QQChat(
        api_base=AMADEUS_CONFIG.onebot_server,
        self_id=self_id,
        chat_type=target_type,
        target_id=target_id,
    )
    qq_chat_instance.save_message(json_body)

    msg_time = json_body.get("time", 0)
    if msg_time:
        logger.trace(
            f"Debounce task scheduled for {green(target_type)} {blue(target_id)}"
        )
        TARGET_STATE[(target_type, target_id)].next_view = msg_time + DEBOUNCE_TIME
    return True


async def _main():
    # Ensure self_id is fetched at startup
    await get_self_id()

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
                else:
                    retry_count += 1
                    wait_time = min(
                        15, 5 + retry_count * 2
                    )
                    logger.info(
                        f"Connection failed, waiting {wait_time}s before retry {retry_count}"
                    )
                    await asyncio.sleep(wait_time)
            except Exception as e:
                retry_count += 1
                current_time = asyncio.get_event_loop().time()

                if current_time - last_log_time >= 60:
                    logger.error(f"Error connecting to IM client at {green(uri)}: {e}")
                    last_log_time = current_time

                wait_time = min(
                    15, 5 + retry_count * 2
                )
                logger.info(
                    f"Connection error, waiting {wait_time}s before retry {retry_count}"
                )
                await asyncio.sleep(wait_time)

    connection_task = asyncio.create_task(connect_with_retry())

    logger.info(
        f"Waiting for {len(_TASKS)} daemon task(s) and connection task to complete..."
    )
    try:
        all_tasks = list(_TASKS.values()) + [connection_task]
        results = await asyncio.gather(*all_tasks, return_exceptions=True)

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