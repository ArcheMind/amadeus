import csv
import socket
import datetime
from io import StringIO
import time


async def iter_csv(line_generator):
    async for line in line_generator:
        reader = csv.reader(StringIO(line), delimiter=" ")
        for row in reader:
            if len(row) < 2:
                continue
            yield row


def self_print(text, **kwargs):
    from loguru import logger

    logger.info(f"\033[92m{text}\033[0m", **kwargs)


def sys_print(text, **kwargs):
    from loguru import logger

    logger.info(f"\033[94m{text}\033[0m", **kwargs)


def green(text):
    return f"\033[92m{text}\033[0m"

def yellow(text):
    return f"\033[93m{text}\033[0m"

def blue(text):
    return f"\033[94m{text}\033[0m"

def red(text):
    return f"\033[91m{text}\033[0m"

def gray(text):
    return f"\033[90m{text}\033[0m"

def format_timestamp(timestamp, timezone=None):
    """
    Format a timestamp into a human-readable string.
    - 刚刚
    - 59分钟前
    - 18:23  (今天之内)
    - 2023-10-01 18:23:00
    """
    now = time.time()
    delta = now - timestamp
    if delta < 30:
        return "刚刚"
    if delta < 60:
        return f"{int(delta)}秒前"
    if delta < 3600:
        return f"{int(delta // 60)}分钟前"

    # Get today's date at midnight for comparison
    today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Use the provided timezone or the current timezone as default
    if timezone:
        dt = datetime.datetime.fromtimestamp(timestamp, tz=timezone)
        today = today.replace(tzinfo=timezone)
    else:
        dt = datetime.datetime.fromtimestamp(timestamp)

    # Check if the timestamp is from today (same calendar day)
    if dt.date() == today.date():
        return dt.strftime("%H:%M")
    else:  # Earlier than today
        return dt.strftime("%Y-%m-%d %H:%M:%S")


class AsyncLruCache:
    def __init__(self, maxsize=128):
        self.cache = {}
        self.maxsize = maxsize

    async def get(self, key):
        return self.cache.get(key)

    async def put(self, key, value):
        if len(self.cache) >= self.maxsize:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = value


def async_lru_cache(maxsize=128):
    def decorator(func):
        cache = AsyncLruCache(maxsize)

        async def wrapper(*args, **kwargs):
            key = (args, tuple(kwargs.items()))
            cached_value = await cache.get(key)
            if cached_value is not None:
                return cached_value
            value = await func(*args, **kwargs)
            await cache.put(key, value)
            return value

        return wrapper

    return decorator


def find_free_ports(n) -> tuple[int, int]:
    sockets = []
    ports = []
    for _ in range(n):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            ports.append(s.getsockname()[1])
            sockets.append(s)
    return tuple(ports)
