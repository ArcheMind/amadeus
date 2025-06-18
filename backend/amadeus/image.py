import hashlib
import json
import random
import base64
import ssl
from amadeus.const import CACHE_DIR
from amadeus.config import AMADEUS_CONFIG
from amadeus.common import async_lru_cache
from loguru import logger
import httpx
from PIL import Image

ANALYZE_VERSION = 1


IMAGE_ANALYZE_CACHE = CACHE_DIR / "cache" / "image" / "analyze" / str(ANALYZE_VERSION)
if not IMAGE_ANALYZE_CACHE.exists():
    IMAGE_ANALYZE_CACHE.mkdir(parents=True)


def get_file_hash(file_path: str) -> str:
    """
    Get the hash of the file using Python
    """
    hash_object = hashlib.md5()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            hash_object.update(chunk)
    file_hash = hash_object.hexdigest()

    return file_hash


def is_gif(path_or_bytes):
    if isinstance(path_or_bytes, str):  # 文件路径
        with open(path_or_bytes, "rb") as f:
            header = f.read(6)
    else:  # 二进制数据
        header = path_or_bytes[:6]
    return header in (b"GIF87a", b"GIF89a")


MEME_MAP = {}


async def analyze_image(image_url):
    logger.info(f"[图片分析] 开始：{image_url}")
    url_hash = await get_image_url_hash(image_url)
    data_path_by_url = IMAGE_ANALYZE_CACHE / f"url_{url_hash}.json"

    if data_path_by_url.exists():
        with open(data_path_by_url, "r", encoding="utf-8") as f:
            logger.info(f"[图片分析] URL命中缓存")
            return json.load(f)

    image_file = await get_image(image_url)
    file_hash = get_file_hash(image_file)
    data_path_by_file = IMAGE_ANALYZE_CACHE / f"file_{file_hash}.json"

    if data_path_by_file.exists():
        logger.info(f"[图片分析] 文件命中缓存")
        with open(data_path_by_file, "r", encoding="utf-8") as f:
            return json.load(f)

    from amadeus.llm import llm

    prompt = """
分析图像，完成以下任务：
1. 识别图像中的文本
2. 把这个图像整体作为表情包
    2.1 它表达的意义是？用词描述三个最显著的维度，如果不确定则输出null
    2.2 这个表情包的风格是？用一个词描述，如果不确定则输出null
3. 描述图片内容（尤其是奇异之处）
4. 如果图像中有人物，描述他们
    4.1. 特征的详细描述，包括但不限于性别、发色、发型、瞳色、穿着，然后是能区别于别的角色的特征
    4.2. 表情
按以下格式输出：
{
    "text": "识别的文本" | null,
    "meme": {
        "meaning": "词1、词2、词3" | null,
        "style": "可爱" | null
    },
    "description": "图片内容描述",
    "people": [
        {
            "characteristics": "人物特征描述",
            "expression": "人物表情描述",
        )
    ]
}
"""
    # if is_gif(image_file):
    #     image = await get_image(image_url, ext="gif")
    # else:
    image = await get_thumbnail(image_url)
    image_b64 = await get_image_b64(image)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_b64}",
                    },
                },
            ],
        },
    ]

    response = ""

    async for chunk in llm(
        messages=messages,
        base_url=AMADEUS_CONFIG.character.vision_model_provider.base_url,
        api_key=AMADEUS_CONFIG.character.vision_model_provider.api_key,
        model=AMADEUS_CONFIG.character.vision_model,
    ):
        response += chunk

    try:
        analyzed_image = json.loads(response)
        with open(data_path_by_file, "w", encoding="utf-8") as f:
            json.dump(analyzed_image, f, ensure_ascii=False, indent=4)
        with open(data_path_by_url, "w", encoding="utf-8") as f:
            json.dump(analyzed_image, f, ensure_ascii=False, indent=4)
        if meaning := analyzed_image.get("meme", {}).get("meaning"):
            if meaning not in MEME_MAP:
                MEME_MAP[meaning] = []
            MEME_MAP[meaning].append(image_b64)
        logger.info(f"[图片分析] 完成：{image_url}")
        return analyzed_image
    except json.JSONDecodeError:
        # Handle the case where the response is not valid JSON
        logger.info(f"[图片分析] 失败：{image_url}")
        return None


@async_lru_cache(maxsize=8000)
async def get_image_b64(image: str) -> str:
    with open(image, "rb") as f:
        image_data = f.read()
    image_b64 = base64.b64encode(image_data).decode("utf-8")
    return image_b64


IMAGE_CACHE = CACHE_DIR / "cache" / "image" / "raw"
if not IMAGE_CACHE.exists():
    IMAGE_CACHE.mkdir(parents=True)


async def get_image_url_hash(image_url: str) -> str:
    """
    Get the hash of the image URL using Python
    """
    hash_object = hashlib.md5()
    hash_object.update(image_url.encode("utf-8"))
    image_hash = hash_object.hexdigest()

    return image_hash


async def get_image(image_url: str, ext="jpg") -> str:
    """
    Get the image from the URL and save it to the cache directory
    """
    image_hash = await get_image_url_hash(image_url)
    image_path = IMAGE_CACHE / f"{image_hash}.{ext}"

    if image_path.exists():
        return str(image_path)

    async with httpx.AsyncClient(verify=SSL_CONTEXT) as client:
        response = await client.get(
            image_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en,zh;q=0.9,zh-CN;q=0.8",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Pragma": "no-cache",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
                "Referer": "https://multimedia.nt.qq.com.cn/",
                "Origin": "https://multimedia.nt.qq.com.cn",
            },
        )
        if response.status_code == 200:
            with open(image_path, "wb") as f:
                f.write(response.content)
            return str(image_path)
        else:
            raise Exception(f"Failed to download image: {response.status_code}")


THUMBNAIL_CACHE = CACHE_DIR / "cache" / "image" / "thumbnail"
if not THUMBNAIL_CACHE.exists():
    THUMBNAIL_CACHE.mkdir(parents=True)
THUMBNAIL_SIZE = 250

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.set_ciphers("DEFAULT@SECLEVEL=1")


async def get_thumbnail(image_url: str, ext="jpg") -> str:
    """
    Get the thumbnail of the image while preserving the original aspect ratio
    """
    image_hash = await get_image_url_hash(image_url)
    thumbnail_path = THUMBNAIL_CACHE / f"{image_hash}.png"

    if thumbnail_path.exists():
        return str(thumbnail_path)

    image_path = await get_image(image_url, ext)
    # if is_gif(image_path):
    #     return image_path

    image = Image.open(image_path)
    width, height = image.size
    if width > height:
        new_width = THUMBNAIL_SIZE
        new_height = int(height * (THUMBNAIL_SIZE / width))
    else:
        new_height = THUMBNAIL_SIZE
        new_width = int(width * (THUMBNAIL_SIZE / height))

    # Resize with proper aspect ratio
    image.thumbnail((new_width, new_height), Image.Resampling.LANCZOS)
    image.save(thumbnail_path, format="PNG", quality=95)

    return str(thumbnail_path)


async def search_meme(meaning: str):
    """
    Search for a meme based on the meaning and style
    """
    if not MEME_MAP:
        thumbnail_paths = list(THUMBNAIL_CACHE.glob("*"))
        for thumbnail_path in thumbnail_paths:
            file_name = thumbnail_path.stem
            analyze_data = IMAGE_ANALYZE_CACHE / f"url_{file_name}.json"
            if analyze_data.exists():
                with open(analyze_data, "r", encoding="utf-8") as f:
                    analyze_data = json.load(f)
                    meme_meaning = (analyze_data.get("meme") or {}).get("meaning")
                    if meme_meaning:
                        if meme_meaning not in MEME_MAP:
                            MEME_MAP[meme_meaning] = []
                        MEME_MAP[meme_meaning].append(
                            await get_image_b64(str(thumbnail_path))
                        )
        logger.info(f"[图片分析] 加载了 {len(MEME_MAP)} 个表情包")
    if memes := MEME_MAP.get(meaning):
        return random.choice(memes)


if __name__ == "__main__":
    import asyncio

    meme = asyncio.run(search_meme("可爱"))

    # image_url = "https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=EhRAN7Vhn76S2MpzGh5u33Z72hBW8RiK9wEg_wookb-X7d2vjQMyBHByb2RQgL2jAVoQMJg9i4kkTB_d-B85yKYLOXoCYtE&rkey=CAQSMBdL8ZstY6roEZ7J2Z02W-7TaCGJl7Fbf4Hda4RTQirAHm0TCx7UuoSkiqyPrDLkPg"
    # thumbnail_path = asyncio.run(get_thumbnail(image_url))
    # print(f"Thumbnail saved at: {thumbnail_path}")
    #
    # analyzed_image = asyncio.run(analyze_image(image_url))
    # print(f"Analyzed image: {analyzed_image}")
    #
