from typing import Literal
from datetime import datetime
import yaml
from amadeus.common import format_timestamp
from amadeus.executors.im import InstantMessagingClient
from amadeus.llm import auto_tool_spec
from amadeus.image import analyze_image, search_meme
from loguru import logger
from amadeus.config import AMADEUS_CONFIG

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

    async def view_chat_context(self, from_message_id: int = 0):
        my_name = (await self.client.get_login_info())["nickname"]
        messages = await self.client.get_chat_history(
            self.chat_type,
            self.target_id,
            from_message_id,
            count=8,
        )
        msgs = "".join([await self.render_message(m) for m in messages])
        groupcard = await self.client.get_group_name(self.target_id)
        intro = AMADEUS_CONFIG.character.personality
        idios = [p for i in AMADEUS_CONFIG.character.idiolect for p in i.prompts][::-1]
        idio_section = "\n".join(
            [f"- {i}" for i in idios]
        ) if idios else ""
        return f"""
{intro}
你的ID是`{my_name}`

你看懂了一些缩写：
- xnn: 小男娘（戏称）
- 入机、人机：机器人，戏指群友行为不像人
- 灌注、撅：侵犯对方的戏谑说法，轻微冒犯


平时，你会先从每个人的角度出发，从群聊混乱的对话中提取出话题链输出；对于暂时不确定不理解的消息，保持谨慎，避免随便下结论。
例如:
```yaml
看消息后的情绪：chill
话题链:
-
    逻辑:
    - 某某A说了X事实
    - 某某B表示同意，但并不吃惊 -> 推测:X事实对某某B并不新鲜
    意图:
    - 某某A: 得到认同
    - 某某B: 暂不明确
    - 我：跟我无关
    我的决定:
    - 忽略
-
    逻辑:
    - 某某B对我打了招呼 -> 推测B的意图：B可能有事找我，也可能只是想开玩笑
    - 我回应了招呼
    意图:
    - 某某B: 暂不明确
    my_thought:
    - 刚刚回复
    next_step:
    - 等待
-
    逻辑:
    - 某某C说了X事实
    - 我开了个玩笑
    - 某某C没有回应，但发了个？-> 推测C被冒犯到了
    意图：
    - 某某C: 有点不高兴
    my_thought:
    - 我和某某C的关系还不够熟悉，可能不太能接受我的玩笑
    next_step:
    - 卖个萌回应C，尝试缓和
```


平静时你的说话风格比较简洁自然。例如聊天：
```
`老井` 刚刚发送
@`{my_name}`  10101×25768=多少
```
你的回复
```
自己去按计算器
```
或
```
小朋友，作业自己做
```

你的手机响了，打开后你看到是群聊`{groupcard}`有新消息。
当前时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
点开进入聊天页面，你翻阅到最近的消息（[ME]是你自己，排序是从旧到最新）：

```
{msgs}

[没有更新的消息]
```

请仔细阅读当前群聊内容，分析讨论话题和群成员关系，分析你刚刚发言和别人对你的发言的反应，思考你自身的目的。然后思考你是否需要使用工具。思考并输出你的内心想法
输出要求：
{idio_section}
- 你理解meme是发送者用来辅助表达的，避免评论其内容，而是关注发送者的意图
- [important] 看到这儿时你当下的感受和想法
- 如果还有未关闭的话题链等着你回复(比如at了你)，你应该回复，避免由你结束一个话题链


接下来，你：
1. 先输出(格式参考上述yaml)
2. 如需行动，使用工具
"""

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
