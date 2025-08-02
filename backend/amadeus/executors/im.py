import websockets
from typing import NoReturn, Any, Callable, Dict, Optional
from loguru import logger
import uuid
import asyncio
import time
from amadeus.tools.context import get_tool_context
from amadeus.kvdb import KVModel
import json

from amadeus.common import (
    async_lru_cache,
    green,
    format_timestamp,
    gray,
    blue,
    red,
    yellow,
)


class WsConnector:
    def __init__(self, uri):
        self.uri = uri
        self.event_handler: Optional[Callable] = None
        self._is_running = False
        self._conn: Optional[websockets.WebSocketClientProtocol] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._connect_lock = asyncio.Lock()

    def register_event_handler(self, handler: Callable):
        self.event_handler = handler

    async def _listen_loop(self) -> NoReturn:
        logger.debug(f"Starting WebSocket listen loop for {green(self.uri)}")
        while self._conn:
            try:
                message = await self._conn.recv()
            except websockets.exceptions.ConnectionClosed:
                logger.warning(
                    f"WebSocket connection to {red(self.uri)} closed. Listen loop will exit."
                )
                break
            try:
                data = json.loads(message)
                logger.trace(
                    f"Received message: {yellow(json.dumps(data, indent=2, ensure_ascii=False))}"
                )
                if self.event_handler:
                    await self.event_handler(data)
            except json.JSONDecodeError:
                logger.warning(f"Received invalid JSON message: {message}")
                continue
        logger.debug(f"WebSocket listen loop ended for {green(self.uri)}")

    async def start(self):
        self._is_running = True
        logger.debug(f"Attempting to connect to WebSocket at {green(self.uri)}")

        # Extract host and port for pre-connection check
        import re
        import socket

        host_port_match = re.search(r"://([^:/]+):(\d+)", self.uri)
        if host_port_match:
            host = host_port_match.group(1)
            port = int(host_port_match.group(2))
            logger.debug(f"Checking if port {port} is open on {host}")

            # Quick TCP connection check with reduced timeout
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)  # Reduced from 2 seconds to 1 second
                result = sock.connect_ex((host, port))
                sock.close()
            except Exception as e:
                logger.warning(f"Failed to check port {port}: {e}")

        try:
            async with self._connect_lock:
                port = host_port_match.group(2) if host_port_match else "unknown"
                logger.debug(f"Trying to establish WebSocket connection to port {port}")

                # Add timeout to WebSocket connection
                self._conn = await asyncio.wait_for(
                    websockets.connect(self.uri),
                    timeout=2.0,  # 2 seconds timeout for WebSocket connection
                )
                logger.info(
                    f"Successfully connected to WebSocket server at {green(self.uri)}"
                )
            self._listen_task = asyncio.create_task(self._listen_loop())
            return True

        except (
            websockets.exceptions.WebSocketException,
            ConnectionRefusedError,
            asyncio.TimeoutError,  # Added timeout exception
        ) as e:
            if host_port_match:
                port = host_port_match.group(2)
            self._conn = None
        except Exception as e:
            logger.error(f"An unexpected error occurred in WsConnector: {e}")
            self._conn = None
        return False

    async def join(self):
        if self._listen_task:
            logger.debug(
                f"Waiting for WebSocket listen task to complete for {green(self.uri)}"
            )
            await self._listen_task
            logger.debug(f"WebSocket listen task completed for {green(self.uri)}")
        else:
            logger.debug(f"No listen task to wait for {green(self.uri)}")

    async def stop(self):
        if self._conn:
            await self._conn.close()
            self._conn = None
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        logger.info(f"WebSocket connection to {green(self.uri)} stopped.")

    async def send(self, data: Dict[str, Any]) -> None:
        async with self._connect_lock:
            if not self._conn:
                raise RuntimeError(
                    f"WebSocket connection to {self.uri} is not established."
                )

        await self._conn.send(json.dumps(data, ensure_ascii=False))
        logger.trace(
            f"Sent message: {yellow(json.dumps(data, indent=2, ensure_ascii=False))}"
        )


class InstantMessagingClient:
    INSTANCES = {}

    def __new__(cls, api_base: str):
        if api_base is None:
            raise ValueError(
                "api_base must be provided for InstantMessagingClient instantiation"
            )
        if api_base not in cls.INSTANCES:
            instance = super().__new__(cls)
            cls.INSTANCES[api_base] = instance
            instance.__init__(api_base)
        return cls.INSTANCES[api_base]

    def __init__(self, api_base: str):
        if getattr(self, "_initialized", False):
            return

        self.api_base = api_base
        if api_base.startswith("ws://") or api_base.startswith("wss://"):
            self._ws_connector: WsConnector = WsConnector(api_base)
            self._response_futures: Dict[str, asyncio.Future] = {}
            self._is_listener_running = False
        self._initialized = True

    async def _ensure_listener_running(self):
        if not self._is_listener_running:
            logger.debug(f"Starting WebSocket connection to {green(self.api_base)}")
            success = await self._ws_connector.start()
            if not success:
                raise RuntimeError(
                    f"WebSocket connection to {self.api_base} is not established."
                )

            self._ws_connector.register_event_handler(self._response_handler)
            self._is_listener_running = True
            logger.info(f"IM client listener started for {green(self.api_base)}")

    async def _response_handler(self, response: Dict[str, Any]):
        echo = response.get("echo")
        if echo in self._response_futures:
            future = self._response_futures[echo]
            if not future.done():
                future.set_result(response)
                logger.trace(f"Response received for echo {blue(echo)}")

    async def _send_request(
        self, action: str, params: Dict[str, Any], timeout: int = 3
    ) -> Dict[str, Any]:
        await self._ensure_listener_running()
        echo = str(uuid.uuid4())
        future = asyncio.get_event_loop().create_future()
        self._response_futures[echo] = future

        request = {"action": action, "params": params, "echo": echo}

        try:
            logger.debug(
                f"Sending request (echo: {blue(echo)}): action={green(action)}, params={yellow(str(params))}"
            )
            await self._ws_connector.send(request)
            response = await asyncio.wait_for(future, timeout=timeout)
            logger.debug(f"Received response for echo {blue(echo)}")
            return response
        except asyncio.TimeoutError:
            logger.error(
                f"Request timed out for echo {blue(echo)} (action: {green(action)})"
            )
            raise
        finally:
            self._response_futures.pop(echo, None)

    async def close(self):
        if self._is_listener_running:
            await self._ws_connector.stop()
            self._is_listener_running = False
            logger.info(f"IM client for {green(self.api_base)} closed.")

    @async_lru_cache()
    async def get_group_name(self, group_id):
        response = await self._send_request(
            action="get_group_info", params={"group_id": str(group_id)}
        )
        if response and response.get("status") == "ok":
            group_name = response["data"]["group_name"]
            return group_name
        else:
            return str(group_id)

    async def get_joined_groups(self):
        logger.debug(f"Starting to get joined groups from {green(self.api_base)}")

        # Check if WebSocket connection is ready
        if not self._is_listener_running:
            logger.debug(
                f"WebSocket listener not running, attempting to start for {green(self.api_base)}"
            )

        try:
            response = await self._send_request(action="get_group_list", params={})
            logger.debug(
                f"Received response from {green(self.api_base)}: status={response.get('status') if response else 'None'}"
            )

            if not (response and response.get("status") == "ok"):
                logger.warning(
                    f"Failed to get groups from {green(self.api_base)}: {response}"
                )
                return []

            groups = response.get("data", [])
            logger.debug(
                f"Successfully got {len(groups)} groups from {green(self.api_base)}"
            )
            return groups
        except Exception as e:
            raise

    @async_lru_cache(maxsize=1)
    async def get_login_info(self):
        response = await self._send_request(action="get_login_info", params={})

        if not (response and response.get("status") == "ok"):
            return None
        return response.get("data")

    @async_lru_cache()
    async def get_user_name(self, user_id):
        response = await self._send_request(
            action="get_stranger_info", params={"user_id": str(user_id)}
        )
        if response and response.get("status") == "ok":
            remark = response["data"].get("remark")
            nickname = response["data"].get("nickname")
            return remark or nickname or str(user_id)
        else:
            return str(user_id)

    @async_lru_cache()
    async def get_group_member_name(self, user_id, group_id):
        response = await self._send_request(
            action="get_group_member_info",
            params={"group_id": str(group_id), "user_id": str(user_id)},
        )
        if response and response.get("status") == "ok":
            data = response["data"]
            card = data.get("card")
            nickname = data.get("nickname")
            return card or nickname or str(user_id)
        else:
            return str(user_id)

    async def set_group_ban(self, group_id: int, user_id: int, duration: int = 0):
        response = await self._send_request(
            action="set_group_ban",
            params={
                "group_id": str(group_id),
                "user_id": str(user_id),
                "duration": duration,
            },
        )
        if response and response.get("status") == "ok":
            return True
        else:
            logger.warning(
                f"Failed to set group ban for user {user_id} in group {group_id}. Response: {response}"
            )
            return False

    async def send_message(self, message, target_type: str, target_id: int):
        if target_type == "group":
            action = "send_group_msg"
            params = {"group_id": target_id, "message": message}
        elif target_type == "private":
            action = "send_private_msg"
            params = {"user_id": target_id, "message": message}
        else:
            return None

        response = await self._send_request(action, params)
        sent_message_info = response.get("data", {})

        if response and response.get("status") == "ok" and sent_message_info:
            # Store the sent message to db to fix the echo problem
            # message_id = sent_message_info.get("message_id")
            # login_info = await self.get_login_info()
            # bot_id = login_info.get("user_id")
            # bot_nickname = login_info.get("nickname")
            # last_view_time = get_tool_context().get("last_view", time.time())
            #
            # own_message = {
            #     "post_type": "message",
            #     "message_type": target_type,
            #     "sender": {
            #         "user_id": bot_id,
            #         "nickname": bot_nickname,
            #         "card": bot_nickname,
            #     },
            #     "message": message,
            #     "time": int(last_view_time),
            #     "message_id": message_id,
            #     "font": 0,
            # }
            #
            # if target_type == "group":
            #     own_message["group_id"] = target_id
            #     own_message["sub_type"] = "normal"
            # elif target_type == "private":
            #     own_message["user_id"] = target_id
            #     own_message["sub_type"] = "friend"
            #
            # message_record_db = KVModel(
            #     self.db_env,
            #     namespace=f"{target_type}_{target_id}",
            #     kind="message_record",
            #     extra_index=["time"],
            # )
            # message_record_db.put(str(message_id), own_message)
            return response.get("data")
        else:
            return None

    async def delete_message(self, message_id):
        response = await self._send_request(
            action="delete_msg", params={"message_id": str(message_id)}
        )
        if response and response.get("status") == "ok":
            return True
        else:
            return False

    @async_lru_cache()
    async def get_message(self, message_id):
        response = await self._send_request(
            action="get_msg", params={"message_id": str(message_id)}
        )
        if response and response.get("status") == "ok":
            return response.get("data")
        else:
            return None

    async def get_chat_history(
        self,
        target_type: str,
        target_id: int,
        till_message_id: int = 0,
        count: int = 20,
    ):
        if target_type == "group":
            action = "get_group_msg_history"
            params = {"group_id": target_id}
        elif target_type == "private":
            action = "get_friend_msg_history"
            params = {"user_id": target_id}
        else:
            return []
        if till_message_id:
            params["message_seq"] = till_message_id

        response = await self._send_request(action, params)
        if response and response.get("status") == "ok":
            return response.get("data", {}).get("messages", [])
        else:
            return []


class QQChat:
    def __init__(self, api_base: str, chat_type: str, target_id: int):
        self.client = InstantMessagingClient(api_base)
        self.chat_type = chat_type
        self.target_id = target_id

    async def send_message(self, content: str):
        """
        在当前会话中发送消息
        """
        await self.client.send_message(
            message=content, chat_type=self.chat_type, target_id=self.target_id
        )

    async def ignore(self):
        """
        忽略当前对话
        """
        pass

    async def set_group_ban(self, user_id: int, duration: int = 60):
        """
        群管理-禁言
        """
        await self.client.set_group_ban(
            group_id=self.target_id, user_id=user_id, duration=duration
        )

    async def delete_message(self, message_id: int):
        """
        撤回消息
        """
        await self.client.delete_message(message_id=message_id)

    async def view_chat_context(self) -> str:
        res = ""
        msgs = await self.client.get_chat_history(
            target_type=self.chat_type, target_id=self.target_id
        )
        my_info = await self.client.get_login_info()
        my_user_id = my_info["user_id"]
        group_name = await self.client.get_group_name(self.target_id)
        res += f"当前群名: {group_name}\n"
        res += "对话记录:\n"
        for msg in msgs:
            user_id = msg["sender"]["user_id"]
            if user_id == my_user_id:
                name = "我"
            else:
                name = await self.client.get_group_member_name(
                    user_id=user_id, group_id=self.target_id
                )
            timestamp = msg.get("time", 0)
            dt = format_timestamp(timestamp)
            res += f"{name} {gray(dt)}:\n  {msg['raw_message']}\n"
        return res
