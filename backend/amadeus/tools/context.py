from datetime import datetime
from typing import List, Dict, Any
from amadeus.config import AMADEUS_CONFIG
from amadeus.tools.im import QQChat


class ChatContext:
    """聊天上下文管理器，负责构建prompt和管理上下文"""

    def __init__(self, chat_type: str, target_id: int, api_base: str):
        self.chat_type = chat_type
        self.target_id = target_id
        self.api_base = api_base
        self._qq_chat = None

    @property
    def qq_chat(self) -> QQChat:
        """延迟初始化QQChat实例"""
        if self._qq_chat is None:
            self._qq_chat = QQChat(
                api_base=self.api_base,
                chat_type=self.chat_type,
                target_id=self.target_id,
            )
        return self._qq_chat

    async def build_chat_messages(
        self, from_message_id: int = 0, last_view: float = None
    ) -> List[Dict[str, Any]]:
        """
        构建聊天上下文的messages，格式为llm可直接使用.
        注意：这是一个临时的实现，将整个prompt塞进了system message
        """
        prompt_string = await self.build_chat_prompt(from_message_id, last_view)
        return [{"role": "system", "content": prompt_string}]

    async def build_chat_prompt(
        self, from_message_id: int = 0, last_view: float = None
    ) -> str:
        """构建聊天上下文的prompt，支持last_view分割线"""
        my_name = (await self.qq_chat.client.get_login_info())["nickname"]
        messages = await self.qq_chat.client.get_chat_history(
            self.chat_type,
            self.target_id,
            from_message_id,
            count=8,
        )
        # 插入[上次你看到这里]分割线
        msgs = await self._render_msgs_with_last_view(messages, last_view)
        groupcard = await self.qq_chat.client.get_group_name(self.target_id)
        intro = AMADEUS_CONFIG.character.personality
        idio_section = self._get_idio_section()
        return self._build_prompt_template(
            my_name, groupcard, msgs, idio_section, intro
        )

    async def _render_msgs_with_last_view(self, messages, last_view):
        """渲染消息并插入分割线"""
        if last_view is None:
            # 如果没有 last_view，直接渲染所有消息
            rendered = []
            for m in messages:
                rendered.append(await self.qq_chat.render_message(m))
            return "".join(rendered)

        # 首先分析消息的时间分布
        has_older = False  # 是否有比 last_view 早的消息
        has_newer = False  # 是否有比 last_view 晚的消息

        for m in messages:
            msg_time = m.get("time", 0)
            if msg_time <= last_view:
                has_older = True
            else:
                has_newer = True

        rendered = []
        inserted = False

        # 情况1：全是比 last_view 新的消息
        if has_newer and not has_older:
            rendered.append("[更早的消息已经看不到了]\n")
            for m in messages:
                rendered.append(await self.qq_chat.render_message(m))

        # 情况2：存在早的也存在晚的消息 - 在转变处插入
        elif has_older and has_newer:
            for m in messages:
                msg_time = m.get("time", 0)
                if not inserted and msg_time > last_view:
                    rendered.append("\n[上次你看到了这里]\n")
                    inserted = True
                rendered.append(await self.qq_chat.render_message(m))

        # 情况3：全是比 last_view 早的消息
        elif has_older and not has_newer:
            for m in messages:
                rendered.append(await self.qq_chat.render_message(m))
            rendered.append("\n[上次你看到了这里]\n")

        # 如果没有消息，直接返回空
        else:
            return ""

        return "".join(rendered)

    def _get_idio_section(self) -> str:
        """获取习语部分"""
        idios = [p for i in AMADEUS_CONFIG.character.idiolect for p in i.prompts][::-1]
        return "\n".join([f"- {i}" for i in idios]) if idios else ""

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
- 如果还有未关闭的话题链等着你回复(比如at了你)，你应该回复，避免由你结束一个话题链


接下来，你：
1. 先输出(格式严格遵循上述yaml)
2. 如需行动，使用工具
"""

    def get_tools(self):
        """获取可用的工具列表"""
        from amadeus.config import AMADEUS_CONFIG

        TOOL_MAP = {
            "撤回消息": self.qq_chat.delete_message,
            "群管理-禁言": self.qq_chat.set_group_ban,
        }

        tools = [TOOL_MAP[t] for t in AMADEUS_CONFIG.enabled_tools if t in TOOL_MAP]

        return [
            self.qq_chat.send_message,
            self.qq_chat.ignore,
        ] + tools

