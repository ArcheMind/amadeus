from contextvars import ContextVar
from contextlib import contextmanager


TOOL_CONTEXT_VAR = ContextVar("tool_context", default=dict())


@contextmanager
def set_tool_context(obj: dict):
    """
    Context manager to temporarily set tool context variables.

    Args:
        **kwargs: Key-value pairs to set in the tool context.

    Yields:
        None: The context is set for the duration of the with block.
    """
    token = TOOL_CONTEXT_VAR.set(obj)
    try:
        yield
    finally:
        TOOL_CONTEXT_VAR.reset(token)


def get_tool_context() -> dict:
    return TOOL_CONTEXT_VAR.get()
