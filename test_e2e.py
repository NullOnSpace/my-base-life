import asyncio
import threading
import time
import uuid


import redis
from bs4 import BeautifulSoup

from base_life.scraper import (
    NewsItem,
    _apply_search_filter,
    setup_logging,
    unfiltered_items,
)
from main import format_news_item, normalize_url, publish_to_redis

REDIS_BASE_CONF = {
    "host": "localhost",
    "port": 6379,
    "db": 0,
    "password": "",
    "channel": "news",
}

ARCHIVE_URL = "file://tests/fixtures/archive_list.html"

SOURCE_CONFIG = {
    "name": "daringfireball",
    "selectors": {
        "list_selector": "div.archive p > a",
        "title": "h1",
        "pub": "h6.dateline",
        "content": "div.article",
    },
}


def _make_redis_conf() -> dict:
    conf = dict(REDIS_BASE_CONF)
    conf["dedup-set"] = f"base-life:test:{uuid.uuid4().hex[:8]}"
    return conf


def _redis_client(redis_conf: dict) -> redis.Redis:
    return redis.Redis(
        host=redis_conf.get("host", "localhost"),
        port=redis_conf.get("port", 6379),
        db=redis_conf.get("db", 0),
        password=redis_conf.get("password") or None,
    )


def _subscribe_messages(
    channel: str, redis_conf: dict, result: list, stop_event: threading.Event
):
    client = _redis_client(redis_conf)
    pubsub = client.pubsub()
    pubsub.subscribe(channel)
    while not stop_event.is_set():
        msg = pubsub.get_message(timeout=1.0)
        if msg and msg["type"] == "message":
            result.append(msg["data"])
        elif msg is None and not stop_event.is_set():
            continue
    pubsub.unsubscribe(channel)
    pubsub.close()
    client.close()


async def _fetch_list_and_details(max_items: int = 3) -> list[NewsItem]:
    # 使用本地文件夹中的 fixtures 来模拟网络请求，避免真实对外请求
    def _read_local_file(path: str) -> str | None:
        try:
            # 支持以 file:// 或相对路径给出
            if path.startswith("file://"):
                rel = path[len("file://") :]
            else:
                rel = path
            with open(rel, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    html = _read_local_file(ARCHIVE_URL)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    links = []
    for a in soup.select("div.archive p > a"):
        href = a.get("href")
        if href:
            # 在 fixtures 中我们使用 example.local 的虚构域名，直接映射到本地文件
            if href.endswith("article-1"):
                links.append("tests/fixtures/article_1.html")
            elif href.endswith("article-2"):
                links.append("tests/fixtures/article_2.html")
            elif href.endswith("article-3"):
                links.append("tests/fixtures/article_3.html")

    selectors = SOURCE_CONFIG["selectors"]
    title_sel = selectors.get("title")
    content_sel = selectors.get("content")
    items = []

    for url in links[:max_items]:
        detail_html = _read_local_file(url)
        if not detail_html:
            continue
        dsoup = BeautifulSoup(detail_html, "lxml")
        title = None
        if title_sel:
            el = dsoup.select_one(title_sel)
            if el:
                title = el.get_text(strip=True)
        content = None
        if content_sel:
            el = dsoup.select_one(content_sel)
            if el:
                content = el.get_text(separator=" ", strip=True)
        pub = None
        pub_sel = SOURCE_CONFIG["selectors"].get("pub")
        if pub_sel:
            pel = dsoup.select_one(pub_sel)
            if pel:
                pub = pel.get_text(strip=True)
        items.append(
            NewsItem(
                source=SOURCE_CONFIG["name"],
                url=url,
                title=title,
                pub=pub,
                content=content,
                filtered=False,
                filter_reason=None,
            )
        )

    return items


def test_normalize_url():
    assert (
        normalize_url("https://example.com/article-1")
        == "https://example.com/article-1"
    )
    assert (
        normalize_url("https://example.com/article-2/")
        == "https://example.com/article-2"
    )
    assert (
        normalize_url("https://example.com/article-3?utm_source=rss")
        == "https://example.com/article-3"
    )
    assert (
        normalize_url("https://example.com/path?q=1&r=2#section")
        == "https://example.com/path"
    )
    assert normalize_url("https://example.com/path/#frag") == "https://example.com/path"
    print("\nnormalize_url 测试通过!")


def test_fetch_and_format():
    setup_logging("INFO")
    items = asyncio.run(_fetch_list_and_details(max_items=3))
    assert len(items) > 0, "应成功抓取至少一条详情页"

    for item in items:
        assert item.title is not None, "详情页应有标题"
        assert item.content is not None, "详情页应有正文内容"
        assert (
            len(item.content) > 100
        ), f"正文内容应超过 100 字符 (实际 {len(item.content)})"

        md = format_news_item(item)
        assert md.startswith("## "), "markdown 应以二级标题开头"
        assert "**来源**: daringfireball" in md
        assert "**链接**:" in md

    print(f"\n抓取+格式化测试通过! 抓取了 {len(items)} 条文章")


async def _test_dedup():
    redis_conf = _make_redis_conf()
    dedup_set = redis_conf["dedup-set"]
    client = _redis_client(redis_conf)

    item1 = NewsItem(
        source="test",
        url="https://example.com/article-1",
        title="Article 1",
        pub=None,
        content="Content 1",
        filtered=False,
        filter_reason=None,
    )
    item2 = NewsItem(
        source="test",
        url="https://example.com/article-2/",
        title="Article 2",
        pub=None,
        content="Content 2",
        filtered=False,
        filter_reason=None,
    )
    item3 = NewsItem(
        source="test",
        url="https://example.com/article-3?utm_source=rss",
        title="Article 3",
        pub=None,
        content="Content 3",
        filtered=False,
        filter_reason=None,
    )

    config = {"redis": redis_conf}
    channel = redis_conf["channel"]

    received: list[bytes] = []
    stop_event = threading.Event()
    sub_thread = threading.Thread(
        target=_subscribe_messages,
        args=(channel, redis_conf, received, stop_event),
        daemon=True,
    )
    sub_thread.start()
    time.sleep(1)

    count1 = await publish_to_redis(config, [item1, item2, item3])
    assert count1 == 3, f"首次发布应为 3 条 (实际 {count1})"
    time.sleep(2)

    assert client.sismember(dedup_set, "https://example.com/article-1")
    assert client.sismember(dedup_set, "https://example.com/article-2")
    assert not client.sismember(
        dedup_set, "https://example.com/article-2/"
    ), "尾部 / 应被标准化去除"
    assert client.sismember(dedup_set, "https://example.com/article-3")
    assert not client.sismember(
        dedup_set, "https://example.com/article-3?utm_source=rss"
    ), "查询字符串应被标准化去除"

    count2 = await publish_to_redis(config, [item1, item2, item3])
    assert count2 == 0, f"重复发布应为 0 条 (实际 {count2})"

    item1_variant = NewsItem(
        source="test",
        url="https://example.com/article-1?foo=bar",
        title="Article 1 Variant",
        pub=None,
        content="Content 1 variant",
        filtered=False,
        filter_reason=None,
    )
    count3 = await publish_to_redis(config, [item1_variant])
    assert count3 == 0, "带查询字符串的相同 URL 也应被去重"
    time.sleep(1)

    stop_event.set()
    sub_thread.join(timeout=5)

    assert len(received) == 3, f"subscriber 应只收到 3 条 (实际 {len(received)})"

    client.delete(dedup_set)
    client.close()

    print(
        "\n去重测试通过! 首次发布 3 条, 重复发布 0 条, URL 标准化正确（去除尾部 / 和查询字符串）"
    )


def test_dedup():
    asyncio.run(_test_dedup())


async def _test_redis_publish_subscribe():
    redis_conf = _make_redis_conf()
    dedup_set = redis_conf["dedup-set"]
    client = _redis_client(redis_conf)

    channel = redis_conf["channel"]
    config = {"redis": redis_conf}

    fake_items = [
        NewsItem(
            source="daringfireball",
            url="https://daringfireball.net/2026/04/test_article",
            title="Test Article One",
            pub=None,
            content="This is test content for E2E verification.",
            filtered=False,
            filter_reason=None,
        ),
        NewsItem(
            source="daringfireball",
            url="https://daringfireball.net/2026/04/test_article_two",
            title="Test Article Two",
            pub=None,
            content="Another test content.",
            filtered=False,
            filter_reason=None,
        ),
    ]

    expected_messages = [format_news_item(item) for item in fake_items]

    received: list[bytes] = []
    stop_event = threading.Event()
    sub_thread = threading.Thread(
        target=_subscribe_messages,
        args=(channel, redis_conf, received, stop_event),
        daemon=True,
    )
    sub_thread.start()
    time.sleep(1)

    count = await publish_to_redis(config, fake_items)
    assert count == len(fake_items)

    time.sleep(3)
    stop_event.set()
    sub_thread.join(timeout=5)

    assert len(received) == len(
        fake_items
    ), f"接收数 {len(received)} != 发布数 {len(fake_items)}"

    for i, raw in enumerate(received):
        decoded = raw.decode("utf-8")
        assert decoded == expected_messages[i], f"第 {i} 条消息不一致"

    client.delete(dedup_set)
    client.close()

    print(
        f"\nRedis pub/sub 测试通过! 发布并接收了 {len(fake_items)} 条消息到 channel '{channel}'"
    )


def test_redis_publish_subscribe():
    asyncio.run(_test_redis_publish_subscribe())


async def _test_e2e_fetch_format_publish():
    redis_conf = _make_redis_conf()
    dedup_set = redis_conf["dedup-set"]
    client = _redis_client(redis_conf)

    setup_logging("INFO")
    config = {"redis": redis_conf}
    channel = redis_conf["channel"]

    items = await _fetch_list_and_details(max_items=3)
    _apply_search_filter(items, ["Apple"])
    matched = unfiltered_items(items)

    assert len(matched) > 0, "搜索 'Apple' 应有匹配结果"

    received: list[bytes] = []
    stop_event = threading.Event()
    sub_thread = threading.Thread(
        target=_subscribe_messages,
        args=(channel, redis_conf, received, stop_event),
        daemon=True,
    )
    sub_thread.start()
    time.sleep(1)

    count = await publish_to_redis(config, matched)
    assert count == len(matched)

    time.sleep(3)
    stop_event.set()
    sub_thread.join(timeout=5)

    assert len(received) == len(
        matched
    ), f"接收数 {len(received)} != 发布数 {len(matched)}"

    for i, raw in enumerate(received):
        decoded = raw.decode("utf-8")
        assert decoded == format_news_item(matched[i])

    client.delete(dedup_set)
    client.close()

    print(
        f"\n完整 E2E 测试通过! 抓取 {len(matched)} 条新闻 -> 去重 -> 格式化 -> 发布到 Redis '{channel}'"
    )


def test_e2e_fetch_format_publish():
    asyncio.run(_test_e2e_fetch_format_publish())


def test_format_news_item():
    item = NewsItem(
        source="test_source",
        url="https://example.com/article",
        title="测试标题",
        pub="2026-04-26T10:30:00",
        content="这是正文内容。",
        filtered=False,
        filter_reason=None,
    )
    md = format_news_item(item)
    assert md.startswith("## 测试标题")
    assert "**来源**: test_source" in md
    assert "**发布时间**: 2026-04-26T10:30:00" in md
    assert "**链接**: https://example.com/article" in md
    assert "这是正文内容。" in md

    item_no_pub = NewsItem(
        source="test_source",
        url="https://example.com/article2",
        title="无日期新闻",
        pub=None,
        content="正文",
        filtered=False,
        filter_reason=None,
    )
    md2 = format_news_item(item_no_pub)
    assert "**发布时间**" not in md2
