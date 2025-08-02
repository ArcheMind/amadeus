import csv
import random
import io
from typing import TYPE_CHECKING, List, Dict
from collections import defaultdict
from pydantic import Field
from loguru import logger

from amadeus.llm import auto_tool_spec
from amadeus.tools.context import get_tool_context
from .dish_data import DISH_CSV_DATA

if TYPE_CHECKING:
    from amadeus.context import ChatContext


def get_all_dishes_by_category() -> Dict[str, List[Dict[str, str]]]:
    """Parses the dish data from the string and groups it by category."""
    # Use .strip() to remove any leading/trailing whitespace, especially the leading newline
    f = io.StringIO(DISH_CSV_DATA.strip())
    reader = csv.DictReader(f)
    dishes_by_category = defaultdict(list)

    for row in reader:
        # Ensure row is not empty and 'category' key exists
        if row and row.get("category"):
            dishes_by_category[row["category"]].append(row)

    # Build a single string for the log message
    log_message_parts = ["Dishes loaded and categorized:"]
    if dishes_by_category:
        for category, dishes in dishes_by_category.items():
            log_message_parts.append(f"- Category '{category}': {len(dishes)} dishes")
    else:
        log_message_parts.append(
            "No dishes found or categories are missing in the data. Check DISH_CSV_DATA format."
        )

    logger.info("\n".join(log_message_parts))

    return dishes_by_category


ALL_DISHES_BY_CATEGORY = get_all_dishes_by_category()


@auto_tool_spec(
    name="send_random_dish",
    description="根据种类(甜点|早餐|午餐|晚餐|夜宵)随机选择一个菜品，并发送菜品名称和图片",
)
async def get_random_dish(
    category: str = Field(..., description="种类，有甜点、早餐、午餐、晚餐"),
):
    if category not in ALL_DISHES_BY_CATEGORY:
        return f"未找到类别为 {category} 的菜品。可用类别: {', '.join(ALL_DISHES_BY_CATEGORY.keys())}"

    dishes_in_category = ALL_DISHES_BY_CATEGORY[category]
    selected_dish = random.choice(dishes_in_category)

    name = selected_dish["name"]
    path = selected_dish["path"]

    image_url = f"https://cdn.babeltower.cn/amadeus/dish/{path}.jpg"
    text = f"为你推荐今天的幸运菜品：{name}"

    chat_context: "ChatContext" = get_tool_context().get("chat_context")
    if not chat_context:
        return "无法获取聊天上下文"

    message_body = [
        {"type": "text", "data": {"text": text}},
        {"type": "image", "data": {"url": image_url}},
    ]

    if await chat_context._qq_chat.send_raw_message(message_body):
        return "已发送"
    else:
        return "发送失败"
