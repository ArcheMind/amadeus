import websockets
from typing import NoReturn, Any, Callable, Dict, Optional
import abc
from loguru import logger
import uuid
import asyncio
import json

import httpx
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
        while self._conn:
            try:
                message = await self._conn.recv()
            except websockets.exceptions.ConnectionClosed:
                logger.warning(
                    f"WebSocket connection to {red(self.uri)} closed. Reconnecting..."
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

    async def start(self):
        self._is_running = True
        try:
            async with self._connect_lock:
                self._conn = await websockets.connect(self.uri)
                logger.info(
                    f"Successfully connected to WebSocket server at {green(self.uri)}"
                )
            self._listen_task = asyncio.create_task(self._listen_loop())

        except (
            websockets.exceptions.WebSocketException,
            ConnectionRefusedError,
        ) as e:
            logger.error(
                f"Failed to connect to WebSocket at {red(self.uri)}: {e}"
            )
            self._conn = None
        except Exception as e:
            logger.error(f"An unexpected error occurred in WsConnector: {e}")
            self._conn = None

    async def join(self):
        if self._listen_task:
            await self._listen_task

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
            await self._ws_connector.start()
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
        self, action: str, params: Dict[str, Any], timeout: int = 10
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
        response = await self._send_request(action="get_group_list", params={})
        if not (response and response.get("status") == "ok"):
            return []
        return response.get("data", [])

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

        if response and response.get("status") == "ok":
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
            res += f'{name} {gray(dt)}:\n  {msg["raw_message"]}\n'
        return res
