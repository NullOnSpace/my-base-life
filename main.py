import asyncio
import logging
import sys
import tomllib
from urllib.parse import urlparse

import redis.asyncio as aioredis

from base_life.scraper import (
    fetch_all_sources,
    setup_logging,
    unfiltered_items,
)


def load_config(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        logging.error("配置文件不存在: %s", path)
        raise SystemExit(1)
    except tomllib.TOMLDecodeError as e:
        logging.error("配置文件格式错误: %s", e)
        raise SystemExit(1)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl().rstrip("/")


def format_news_item(item) -> str:
    lines = []
    if item.title:
        lines.append(f"## {item.title}")
    else:
        lines.append("## [无标题]")
    lines.append("")
    meta = f"**来源**: {item.source}"
    if item.pub:
        meta += f"  |  **发布时间**: {item.pub}"
    lines.append(meta)
    lines.append("")
    lines.append(f"**链接**: {item.url}")
    lines.append("")
    if item.content:
        lines.append(item.content.strip())
    lines.append("")
    return "\n".join(lines)


async def publish_to_redis(config: dict, items: list) -> int:
    redis_conf = config.get("redis", {})
    host = redis_conf.get("host", "localhost")
    port = redis_conf.get("port", 6379)
    db = redis_conf.get("db", 0)
    password = redis_conf.get("password") or None
    channel = redis_conf.get("channel", "news")
    dedup_set = redis_conf.get("dedup-set", "base-life:published")

    client = aioredis.Redis(host=host, port=port, db=db, password=password)
    try:
        pipe = client.pipeline()
        for item in items:
            pipe.sismember(dedup_set, normalize_url(item.url))
        exists = await pipe.execute()

        count = 0
        pipe = client.pipeline()
        for i, item in enumerate(items):
            if exists[i]:
                continue
            msg = format_news_item(item)
            pipe.publish(channel, msg)
            pipe.sadd(dedup_set, normalize_url(item.url))
            count += 1
        if count:
            await pipe.execute()
        return count
    except aioredis.RedisError:
        logging.exception("Redis 操作失败")
        return 0
    finally:
        await client.aclose()


async def run(config_path: str):
    config = load_config(config_path)

    log_level = config.get("logging", {}).get("level", "INFO")
    setup_logging(log_level)

    sources = config.get("sources", [])
    if not sources:
        logging.warning("配置中没有 sources，退出。")
        return

    items = await fetch_all_sources(sources)
    matched = unfiltered_items(items)

    if not matched:
        logging.info("没有匹配的新闻。")
        return

    count = await publish_to_redis(config, matched)
    logging.info("已发布 %d 条新闻到 Redis channel（去重后）。", count)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    asyncio.run(run(config_path))
