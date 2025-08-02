import asyncio
import csv
import random
import io
from typing import Literal, TYPE_CHECKING, List, Dict

from pydantic import BaseModel, Field

from amadeus.llm import auto_tool_spec
from amadeus.tools.context import get_tool_context
from .dish_data import DISH_CSV_DATA

if TYPE_CHECKING:
    from amadeus.context import ChatContext

def get_all_dishes() -> List[Dict[str, str]]:
    """Parses the dish data from the string."""
    f = io.StringIO(DISH_CSV_DATA)
    reader = csv.DictReader(f)
    return list(reader)

ALL_DISHES = get_all_dishes()


class DishToolArgs(BaseModel):
    category: str = Field(..., description="种类，有甜点、早餐、午餐、晚餐")

@auto_tool_spec(
    name="send_random_dish",
    description="根据种类(甜点|早餐|午餐|晚餐|夜宵)随机选择一个菜品，并发送菜品名称和图片",
)
async def get_random_dish(args: DishToolArgs) -> str:
    """
    根据类别随机选择一个菜品，并发送菜品名称和图片
    """
    dishes_in_category = [d for d in ALL_DISHES if d['category'] == args.category]
    
    if not dishes_in_category:
        return f"未找到类别为 {args.category} 的菜品"
    
    selected_dish = random.choice(dishes_in_category)
    
    name = selected_dish["name"]
    path = selected_dish["path"]
    
    image_url = f"https://cdn.babeltower.cn/amadeus/dish/{path}.jpg"
    
    text = f"为你推荐今天的幸运菜品：{name}"

    message_body = [
        {"type": "text", "data": {"text": text}},
        {"type": "image", "data": {"url": image_url}},
    ]

    context = get_tool_context()
    chat_context: "ChatContext" = context["chat_context"]

    await chat_context.qq_chat.client.send_message(
        message_body,
        chat_context.chat_type,
        chat_context.target_id,
