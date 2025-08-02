from openai import AsyncOpenAI
import asyncio
import json
import inspect
import yaml
from amadeus.config import AMADEUS_CONFIG
from amadeus.common import green
from typing import (
    Dict,
    Any,
    List,
    Literal,
    Optional,
    AsyncIterator,
    get_type_hints,
    get_origin,
    get_args,
)
from pydantic import BaseModel
from loguru import logger


async def llm(
    messages,
    base_url=None,
    api_key=None,
    model=None,
    tools=None,
    continue_on_tool_call=False,
    temperature=0.7,
) -> AsyncIterator[str]:
    if base_url is None:
        base_url = AMADEUS_CONFIG.character.chat_model_provider.base_url
    if api_key is None:
        api_key = AMADEUS_CONFIG.character.chat_model_provider.api_key
    if model is None:
        model = AMADEUS_CONFIG.character.chat_model

    logger.info("[开始思考]")
    tools = tools or []
    tool_specs = [t.tool_spec.model_dump(exclude_none=True) for t in tools]
    tool_handlers = {t.tool_spec.function.name: t for t in tools}

    openai_client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
    )

    try:
        for _ in range(20):
            messages_log_parts = []
            for msg in messages:
                role = msg.get("role", "unknown").upper()
                content = str(msg.get("content") or "").strip()
                if role == "USER":
                    messages_log_parts.append(content)
                else:
                    messages_log_parts.append(green(content))

            messages_log = "\n\n".join(messages_log_parts)
            logger.info(
                f"======================= [思考中... 消息数: {len(messages)} 工具数: {len(tool_specs)}] =======================\n"
                f"{messages_log}"
            )
            if not tool_specs:
                response = await openai_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,
                    temperature=temperature,
                    # top_p=0.9,
                    max_tokens=4096,
                )
            else:
                response = await openai_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tool_specs,
                    stream=True,
                    temperature=temperature,
                    # top_p=0.9,
                    tool_choice="auto",
                    max_tokens=4096,
                )

            sentence = []
            current_tool_calls = {}

            async for chunk in response:
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                if hasattr(delta, "content") and delta.content:
                    d = delta.content
                    if d is None:
                        continue
                    if "\n" in d:
                        current_word, next_word = d.split("\n", 1)
                        sentence += current_word
                        yield "".join(sentence).strip()
                        sentence = [next_word]
                    else:
                        sentence += d

                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    for tool_call_delta in delta.tool_calls:
                        # Initialize tool call if it's new
                        if tool_call_delta.index not in current_tool_calls:
                            current_tool_calls[tool_call_delta.index] = {
                                "id": tool_call_delta.id or "",
                                "type": tool_call_delta.type,
                                "function": {"name": "", "arguments": ""},
                            }

                        # Update tool call data
                        if tool_call_delta.id:
                            current_tool_calls[tool_call_delta.index]["id"] = (
                                tool_call_delta.id
                            )

                        if tool_call_delta.function:
                            if tool_call_delta.function.name:
                                current_tool_calls[tool_call_delta.index]["function"][
                                    "name"
                                ] = tool_call_delta.function.name
                            if tool_call_delta.function.arguments:
                                current_tool_calls[tool_call_delta.index]["function"][
                                    "arguments"
                                ] += tool_call_delta.function.arguments
            if sentence:
                yield "".join(sentence).strip()
                sentence = []

            if current_tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": list(current_tool_calls.values()),
                        "content": None,
                    }
                )
                for _, tool_call in current_tool_calls.items():
                    tool_name = tool_call["function"]["name"]
                    logger.info(
                        f"[调用工具：[{tool_name}({tool_call['function']['arguments']})]]"
                    )
                    if tool_name in tool_handlers:
                        handler = tool_handlers[tool_name]
                        arguments = json.loads(tool_call["function"]["arguments"])

                        result = await handler(**arguments)

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "name": tool_name,
                                "content": str(result),
                            }
                        )
                        logger.info(f"[工具调用完成：{result}]")

                if continue_on_tool_call:
                    continue
                else:
                    logger.info("[结束思考]")
                    break
            break

    except Exception:
        import traceback

        logger.error("[LLM发生错误]")
        logger.error(f"LLM error details: {traceback.format_exc()}")
        if "response" in locals():
            logger.error(f"LLM response context: {locals()['response']}")

        yield "[推理失败]"


class ToolParameterProperty(BaseModel):
    type: str
    description: Optional[str] = None
    enum: Optional[List[str]] = None
    items: Optional[Dict[str, Any]] = None  # For arrays
    properties: Optional[Dict[str, Any]] = None  # For nested objects
    required: Optional[List[str]] = None


class ToolParameters(BaseModel):
    type: Literal["object"] = "object"
    properties: Dict[str, ToolParameterProperty]
    required: Optional[List[str]] = None


class Function(BaseModel):
    name: str
    description: Optional[str]
    parameters: ToolParameters


class FunctionItem(BaseModel):
    type: Literal["function"] = "function"
    function: Function


def tool_spec(
    name: str,
    description: str,
    parameters: ToolParameters,
):
    """
    Decorator to define a function as a tool for the LLM.
    """

    def decorator(func):
        # Create a FunctionDefinition object
        function_definition = FunctionItem(
            function=Function(
                name=name,
                description=description,
                parameters=parameters,
            )
        )

        # Set the tool item as an attribute of the function
        setattr(func, "tool_spec", function_definition)
        return func

    return decorator


def _get_type_property(annotation) -> Dict[str, Any]:
    """Convert Python type annotation to OpenAI tool parameter property."""
    if isinstance(annotation, type):
        if issubclass(annotation, str):
            return {"type": "string"}
        elif issubclass(annotation, int):
            return {"type": "integer"}
        elif issubclass(annotation, float):
            return {"type": "number"}
        elif issubclass(annotation, bool):
            return {"type": "boolean"}
        elif issubclass(annotation, list):
            return {"type": "array", "items": {"type": "string"}}
        elif issubclass(annotation, dict):
            return {"type": "object"}
    # Check for Literal types, use as enum
    if get_origin(annotation) is Literal:
        enum_values = get_args(annotation)
        return {
            "type": "string",
            "enum": [value for value in enum_values if isinstance(value, str)],
        }
    # Default to string for unknown types
    return {"type": "string"}


def auto_tool_spec(
    name: Optional[str] = None,
    description: Optional[str] = None,
):
    """
    Decorator that automatically generates tool spec from function type annotations.

    Args:
        name: Optional name for the tool (defaults to function name)
        description: Optional description (defaults to function docstring)
    """

    def decorator(func):
        # Get function name
        func_name = name or func.__name__

        # Get function description from docstring
        func_description = description or (inspect.getdoc(func) or "").strip()

        # Get type hints
        type_hints = get_type_hints(func)

        # Get function signature
        sig = inspect.signature(func)

        # Build properties dictionary
        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            # Skip self and cls for methods
            if param_name in ("self", "cls"):
                continue

            param_type = type_hints.get(param_name, str)
            param_doc = ""

            # Check if parameter has a default value
            has_default = param.default != inspect.Parameter.empty
            if not has_default:
                required.append(param_name)

            # Create parameter property
            param_property = _get_type_property(param_type)

            # Add description if available
            if param_doc:
                param_property["description"] = param_doc

            properties[param_name] = param_property

        # Create parameters object
        parameters = ToolParameters(
            properties={
                name: ToolParameterProperty(**prop) for name, prop in properties.items()
            },
            required=required if required else None,
        )

        # Create function definition
        function_definition = FunctionItem(
            function=Function(
                name=func_name,
                description=func_description,
                parameters=parameters,
            )
        )

        # Set the tool item as an attribute of the function
        setattr(func, "tool_spec", function_definition)
        return func

    return decorator


if __name__ == "__main__":
    # Example 1: Manual tool_spec
    @tool_spec(
        name="get_current_weather",
        description="Get the current weather in a given location",
        parameters=ToolParameters(
            properties={
                "location": ToolParameterProperty(
                    type="string",
                    description="The location to get the weather for",
                ),
                "unit": ToolParameterProperty(
                    type="string",
                    description="The unit of temperature (Celsius or Fahrenheit)",
                    enum=["Celsius", "Fahrenheit"],
                ),
            },
            required=["location", "unit"],
        ),
    )
    async def get_current_weather(location: str, unit: str):
        """Get the current weather for a location."""
        # In a real implementation, this would call a weather API
        temperature = "25" if unit == "Celsius" else "77"
        return f"The weather in {location} is sunny with a temperature of {temperature}°{unit[0]}"

    # Example 2: Automatic tool_spec using type annotations
    @auto_tool_spec(description="查询天气")
    async def get_current_weather_auto(
        location: str,
        unit: Literal["Celsius", "Fahrenheit"] = "Celsius",
    ):
        """Get the current weather for a location."""
        # In a real implementation, this would call a weather API
        temperature = "25" if unit == "Celsius" else "77"
        return f"The weather in {location} is sunny with a temperature of {temperature}°{unit[0]}"

    # call llm with tool handlers
    async def main():
        messages = [{"role": "user", "content": "Hello, 能不能帮我查一下上海的天气？"}]
        async for response in llm(
            messages,
            tools=[get_current_weather_auto],
            continue_on_tool_call=True,
        ):
            logger.info(green(response))

    asyncio.run(main())
