from datetime import datetime
from typing import List, Dict, Any, Optional
import lmdb
import os
import collections
from amadeus.const import DATA_DIR
from amadeus.kvdb import KVModel
from amadeus.config import AMADEUS_CONFIG
from amadeus.tools.im import QQChat
from amadeus.tools.dish import get_random_dish
from amadeus.executors.im import InstantMessagingClient
from loguru import logger

# This db_env is for storing 'thinking' processes, separate from message history.
db_env = lmdb.open(os.path.join(DATA_DIR, "thinking.mdb"), map_size=100 * 1024**2)


class ChatContext:
    """聊天上下文管理器，负责构建prompt和管理上下文"""

    def __init__(self, chat_type: str, target_id: int, api_base: str):
        self.chat_type = chat_type
        self.target_id = target_id
        self.api_base = api_base
        self.client = InstantMessagingClient(api_base)

        self._qq_chat: Optional[QQChat] = None
        self._self_id: Optional[int] = None
        self._my_name: Optional[str] = None

    async def _initialize_bot_info(self):
        """Initializes self_id and my_name if not already done."""
        if self._self_id is None or self._my_name is None:
            try:
                info = await self.client.get_login_info()
                self._self_id = info["user_id"]
                self._my_name = info["nickname"]
            except Exception as e:
                logger.error(f"Failed to get login info: {e}")
                self._self_id = -1
                self._my_name = "Amadeus"

    async def ensure_qq_chat_instance(self) -> QQChat:
        """
        Ensures the QQChat instance is created, returning it.
        This should be called before accessing get_tools or build_chat_messages.
        """
        await self._initialize_bot_info()
        if self._qq_chat is None:
            self._qq_chat = QQChat(
                api_base=self.api_base,
                self_id=self._self_id,
                chat_type=self.chat_type,
                target_id=self.target_id,
            )
        return self._qq_chat

    def _aggregate_user_messages(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """将连续的user消息聚合到一起"""
        if not messages:
            return []

        aggregated = []
        for msg in messages:
            content = msg.get("content", "")
            is_special_marker = (
                "[上次你看到了这里]" in content or "[更早的消息已经看不到了]" in content
            )

            if (
                aggregated
                and aggregated[-1]["role"] == "user"
                and msg["role"] == "user"
                and not is_special_marker
                and "[上次你看到了这里]" not in aggregated[-1].get("content", "")
                and "[更早的消息已经看不到了]" not in aggregated[-1].get("content", "")
            ):
                aggregated[-1]["content"] += f"\n{content}"
            else:
                aggregated.append(msg.copy())
        return aggregated

    async def build_chat_messages(
        self, last_view: float = None
    ) -> List[Dict[str, Any]]:
        """
        构建聊天上下文的messages，格式为llm可直接使用.
        """
        qq_chat_instance = await self.ensure_qq_chat_instance()

        messages = qq_chat_instance.get_history(limit=12)

        groupcard = (
            await qq_chat_instance.client.get_group_name(self.target_id)
            if self.chat_type == "group"
            else "私聊"
        )
        intro = AMADEUS_CONFIG.character.personality
        idio_section = self._get_idio_section()

        placeholder = "%%MSGS%%"
        prompt_shell = self._build_prompt_template(
            self._my_name, groupcard, placeholder, idio_section, intro
        )

        prefix, suffix = prompt_shell.split(placeholder)

        final_messages = [{"role": "system", "content": prefix.strip()}]
        raw_history_messages = []

        if last_view is None:
            for m in messages:
                role = (
                    "assistant"
                    if m.get("sender", {}).get("user_id") == self._self_id
                    else "user"
                )
                content = await qq_chat_instance.render_message(m)
                raw_history_messages.append({"role": role, "content": content})
        else:
            has_older = any(m.get("time", 0) <= last_view for m in messages)
            has_newer = any(m.get("time", 0) > last_view for m in messages)

            thinking_db = KVModel(
                db_env, namespace=f"{self.chat_type}_{self.target_id}", kind="thinking"
            )
            last_thought = thinking_db.get("last_thought") or ""

            if has_newer and not has_older:
                raw_history_messages.append(
                    {"role": "assistant", "content": "[更早的消息已经看不到了]"}
                )
                for m in messages:
                    role = (
                        "assistant"
                        if m.get("sender", {}).get("user_id") == self._self_id
                        else "user"
                    )
                    content = await qq_chat_instance.render_message(m)
                    raw_history_messages.append({"role": role, "content": content})

            elif has_older and has_newer:
                inserted = False
                for m in messages:
                    msg_time = m.get("time", 0)
                    if not inserted and msg_time > last_view:
                        raw_history_messages.append(
                            {
                                "role": "assistant",
                                "content": f"\n[上次你看到了这里]\n{last_thought}\n",
                            }
                        )
                        inserted = True
                    role = (
                        "assistant"
                        if m.get("sender", {}).get("user_id") == self._self_id
                        else "user"
                    )
                    content = await qq_chat_instance.render_message(m)
                    raw_history_messages.append({"role": role, "content": content})

            elif has_older and not has_newer:
                for m in messages:
                    role = (
                        "assistant"
                        if m.get("sender", {}).get("user_id") == self._self_id
                        else "user"
                    )
                    content = await qq_chat_instance.render_message(m)
                    raw_history_messages.append({"role": role, "content": content})
                raw_history_messages.append(
                    {
                        "role": "assistant",
                        "content": f"\n[上次你看到了这里]\n{last_thought}\n",
                    }
                )

        history_as_messages = self._aggregate_user_messages(raw_history_messages)
        final_messages.extend(history_as_messages)
        final_messages.append({"role": "system", "content": suffix.strip()})
        return final_messages

    def _get_idio_section(self) -> str:
        """获取习语部分"""
        idios = [p for i in AMADEUS_CONFIG.character.idiolect for p in i.prompts][::-1]
        return "\n".join([f"- {i.strip()}" for i in idios]) if idios else ""

    def _build_prompt_template(
        self, my_name: str, groupcard: str, msgs: str, idio_section: str, intro: str
    ) -> str:
        """构建prompt模板"""
        return f"""
{intro}
你的ID是`{my_name}`

你看懂了一些缩写：
- xnn: 小男娘（戏称）
- 入机、人机：机器人，戏指群友行为不像人
- 灌注、撅：侵犯对方的戏谑说法，轻微冒犯


平时，你会先从每个人的角度出发，从群聊混乱的对话中提取出话题链输出；对于暂时不确定不理解的消息，保持谨慎，避免随便下结论。
例如:
``` yaml
情绪：chill
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

[到底了]
```

你通常的说话风格：
{idio_section}


接下来，你：
1. 一定会先输出思考(格式严格遵循yaml示例)
2. 然后，如需行动，一定会调用工具
"""

    def get_tools(self) -> list:
        """
        获取可用的工具列表.
        前提: ensure_qq_chat_instance() 必须先被调用.
        """
        if self._qq_chat is None:
            raise RuntimeError(
                "QQChat instance has not been initialized. Call ensure_qq_chat_instance() first."
            )

        from amadeus.config import AMADEUS_CONFIG

        TOOL_MAP = {
            "撤回消息": self._qq_chat.delete_message,
            "群管理-禁言": self._qq_chat.set_group_ban,
            "互动-点菜": get_random_dish,
        }

        tools = [TOOL_MAP[t] for t in AMADEUS_CONFIG.enabled_tools if t in TOOL_MAP]

        return [
            self._qq_chat.send_message,
            self._qq_chat.ignore,
        ] + tools
