from typing import Literal
import yaml
from amadeus.common import format_timestamp
from amadeus.executors.im import InstantMessagingClient
from amadeus.llm import auto_tool_spec
from amadeus.image import analyze_image, search_meme
from loguru import logger

from bs4 import BeautifulSoup


def parse_xml_element(text):
    try:
        soup = BeautifulSoup(text, "xml")
        element = soup.find()
        if element:
            return element
        else:
            return None
    except Exception as e:
        from loguru import logger
        logger.debug(f"Failed to parse XML element from text: {e}")
        return None


class QQChat:
    def __init__(
        self,
        api_base,
        chat_type: Literal["group", "private"],
        target_id: int,
    ):
        self.client = InstantMessagingClient(api_base)
        self.chat_type = chat_type
        self.target_id = target_id

    @auto_tool_spec(
        name="reply",
        description="回复消息（只在消息流混乱时使用引用）",
    )
    async def send_message(self, text: str, refer_msg_id: int = 0):
        if xml := parse_xml_element(text):
            logger.info(f"解析到XML元素: {text}")
            refer_msg_id = 0
            if xml.name not in ["meme", "at"]:
                logger.info(f"无法解析的XML元素: {xml.name}")
                return False
            # 获取xml的meaning属性
            meme_b64 = await search_meme(xml["meaning"])
            if not meme_b64:
                logger.info(f"无法找到表情包: {xml['meaning']}")
                return False
            message_body = [
                {
                    "type": "image",
                    "data": {
                        "file": f"base64://{meme_b64}",
                    },
                }
            ]
        else:
            message_body = [
                {"type": "text", "data": {"text": text}},
            ]

        if refer_msg_id:
            message_body.insert(
                0,
                {
                    "type": "reply",
                    "data": {
                        "id": refer_msg_id,
                        "type": self.chat_type,
                    },
                },
            )

        if self.chat_type == "group":
            await self.client.send_message(
                message_body,
                "group",
                self.target_id,
            )
        elif self.chat_type == "private":
            await self.client.send_message(
                message_body,
                "private",
                self.target_id,
            )
        return True

    @auto_tool_spec(
        name="ban",
        description="禁言用户（秒）。适用于：用户发言不当/刷屏/恶意攻击",
    )
    async def set_group_ban(
        self,
        user_id: int,
        duration: int = 300,
    ):
        if self.chat_type != "group":
            raise ValueError("只能在群聊中使用禁言功能")
        return await self.client.set_group_ban(
            self.target_id,
            user_id,
            duration,
        )

    @auto_tool_spec(
        name="no_reply",
        description="不回复。适用于：话题无关/无聊/不感兴趣；最后一条消息是你自己发的且无人回应你；讨论你不懂的话题",
    )
    async def ignore(self):
        return True

    @auto_tool_spec(
        name="recall",
        description="撤回消息",
    )
    async def delete_message(self, message_id: int):
        return await self.client.delete_message(message_id)



    async def get_usercard(self, user_id: int):
        self_id = (await self.client.get_login_info())["user_id"]
        if int(user_id) == int(self_id):
            return "[ME]"
        usercard = None
        if self.chat_type == "group":
            usercard = await self.client.get_group_member_name(
                user_id,
                group_id=self.target_id,
            )

        if self.chat_type == "private":
            usercard = await self.client.get_user_name(user_id)

        return f"`{usercard}({user_id})`" if usercard else f"`{user_id}`"

    async def decode_message_item(self, message):
        if message["type"] == "at":
            user_name = await self.get_usercard(message["data"]["qq"])
            return f"<at user={user_name}/> "
        if message["type"] == "text":
            return message["data"]["text"]
        if message["type"] == "reply":
            msg_id = message["data"]["id"]
            if (msg := await self.client.get_message(msg_id)) is not None:
                refer_msg_content = await self.render_message(msg)
                refer_msg = "\n> ".join(refer_msg_content.strip().split("\n"))
                return f"> <引用消息 id={msg_id}/>\n> {refer_msg}\n"
            return ""
        if message["type"] == "image":
            data = await analyze_image(message["data"]["url"])
            if data and data.get("meme", {}).get("meaning"):
                meaning = data["meme"]["meaning"]
                text = (data.get("text") or "").replace("\n", " ")
                description = data["description"]
                return f'<meme meaning="{meaning}" text="{text}" description="{description}"/>'
            elif data and data.get("description"):
                text = (data.get("text") or "").replace("\n", " ")
                description = data["description"]
                return f'<image description="{description}" text="{text}"/>'
        return ""

    async def render_message(self, data: dict):
        sender = data.get("sender", 0)
        time = data.get("time", 0)
        if data.get("post_type") == "notice":
            if data.get("notice_type") == "notify":
                noticer = await self.get_usercard(sender["user_id"])
                assert data.get("target_id"), yaml.dump(data)
                noticee = await self.get_usercard(data["target_id"])
                action1 = data["raw_info"][2]["txt"]
                action2 = data["raw_info"][4]["txt"]
                if action2:
                    return f"""
{noticer} {format_timestamp(time)}
[拍了拍{noticee}({noticee}设置的拍一拍格式是"对方{action1}自己{action2}")]
"""
                else:
                    return f"""
{noticer} {format_timestamp(time)}
[{action1} {noticee}]
"""
            elif data.get("notice_type") == "group_recall":
                user = await self.get_usercard(sender["user_id"])
                return f"""
{user} {format_timestamp(time)}
[撤回消息]
"""
            else:
                return "[无法解析的通知]"
        elif data.get("post_type") in ("message", "message_sent"):
            message_items = data.get("message", [])
            decoded_items = [await self.decode_message_item(i) for i in message_items]
            message_content = "".join(decoded_items)
            user_card = await self.get_usercard(sender["user_id"])
            return f"""
{user_card} {format_timestamp(time)}发送 id:{data["message_id"]}
{message_content}
"""
        else:
            logger.info(yaml.dump(data, indent=2, allow_unicode=True))
            return "[无法解析的消息]"
