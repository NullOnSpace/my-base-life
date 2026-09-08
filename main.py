"""my-base-life：A 层爬虫信源接入（编排 base-life 库）。

旧 Markdown 模板（已随 Phase 1 从发布路径移除，Phase 3 由 render-hub 的
NEWS 渲染器按此版式复用，渲染效果不变）：

    ## {title}
    **来源**: {source}  |  **发布时间**: {pub}
    **链接**: {url}

    {content}
"""

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
from canonical_msg import build_canonical, matched_keywords, protojson


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


async def publish_to_redis(config: dict, items: list) -> int:
    redis_conf = config.get("redis", {})
    host = redis_conf.get("host", "localhost")
    port = redis_conf.get("port", 6379)
    db = redis_conf.get("db", 0)
    password = redis_conf.get("password") or None
    channel = redis_conf.get("canonical-channel", "msgs.in")
    dedup_set = redis_conf.get("dedup-set", "base-life:published")

    if redis_conf.get("channel"):
        logging.warning(
            "redis.channel 已弃用且被忽略，发布目标为 canonical-channel"
        )

    source_selectors = {
        src.get("name"): src.get("selectors", {})
        for src in config.get("sources", [])
    }

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
            try:
                msg = build_canonical(
                    item,
                    matched_keywords(
                        item, source_selectors.get(item.source, {})
                    ),
                )
            except Exception:
                logging.exception("canonical 构造失败，跳过该条: %s", item.url)
                continue
            pipe.publish(channel, msg.SerializeToString())
            pipe.sadd(dedup_set, normalize_url(item.url))
            count += 1
            logging.debug("canonical %s", protojson(msg))
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
    logging.info("已发布 %d 条 canonical 消息到 Redis（去重后）。", count)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    asyncio.run(run(config_path))
