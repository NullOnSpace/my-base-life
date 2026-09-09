import asyncio
import time
import uuid


import pytest
import redis
from bs4 import BeautifulSoup

from base_life.scraper import (
    NewsItem,
    _apply_search_filter,
    setup_logging,
    unfiltered_items,
)
from canonical_msg import build_canonical, canonical, matched_keywords
from main import normalize_url, publish_to_redis

REDIS_BASE_CONF = {
    "host": "localhost",
    "port": 6379,
    "db": 0,
    "password": "",
}

ARCHIVE_URL = "file://tests/fixtures/archive_list.html"

SOURCE_CONFIG = {
    "name": "daringfireball",
    "selectors": {
        "list_selector": "div.archive p > a",
        "title": "h1",
        "pub": "h6.dateline",
        "content": "div.article",
        "search": ["Apple"],
    },
}


def _make_redis_conf() -> dict:
    conf = dict(REDIS_BASE_CONF)
    conf["dedup-set"] = f"base-life:test:{uuid.uuid4().hex[:8]}"
    conf["msgs_stream"] = f"base-life:test:{uuid.uuid4().hex[:8]}"
    return conf


def _redis_client(redis_conf: dict) -> redis.Redis:
    return redis.Redis(
        host=redis_conf.get("host", "localhost"),
        port=redis_conf.get("port", 6379),
        db=redis_conf.get("db", 0),
        password=redis_conf.get("password") or None,
    )


def _xrange_messages(stream: str, redis_conf: dict) -> list[bytes]:
    """读回断言：直接 XRANGE 取回 data field（生产消费归 push-core，测试简化）。

    校验契约：entry 仅含 "data" 一个 field（与 streams-lib DATA_FIELD 一致）。
    """
    client = _redis_client(redis_conf)
    try:
        entries = client.xrange(stream, min="-", max="+")
        raws = []
        for entry_id, fields in entries:
            assert set(fields) == {b"data"}, (
                f"entry {entry_id!r} 应只含 data field，实际 {set(fields)}"
            )
            raws.append(fields[b"data"])
        return raws
    finally:
        client.close()


def _assert_canonical_fields(
    msg: canonical.CanonicalMessage, item: NewsItem, matched: list[str]
) -> None:
    assert msg.id and len(msg.id) == 26, "id 应为 ULID 字符串"
    assert msg.source.kind == canonical.SOURCE_KIND_CRAWLER
    assert msg.source.id == f"base-life:{item.source}"
    assert msg.source.display_name == item.source
    assert msg.kind == canonical.MSG_KIND_NEWS
    assert msg.dedup_key == f"base-life:{item.source}:{normalize_url(item.url)}"
    assert msg.title == (item.title or "")
    expected_body = (item.content or "").strip()
    assert msg.body_markdown == expected_body
    assert msg.body_text == expected_body
    assert [(link.rel, link.url) for link in msg.links] == [
        ("original", item.url)
    ]
    assert list(msg.tags) == matched
    assert msg.HasField("published_at"), "published_at 永不为空"


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
    assert (
        normalize_url("https://example.com/path/#frag")
        == "https://example.com/path"
    )
    print("\nnormalize_url 测试通过!")


def test_matched_keywords():
    item = NewsItem(
        source="test",
        url="https://example.com/a",
        title="Apple MacBook 新品发布",
        content="",
    )
    selectors = {"search": ["apple", "MacBook", "apple"]}
    assert matched_keywords(item, selectors) == ["apple", "MacBook"]

    item2 = NewsItem(
        source="test",
        url="https://example.com/b",
        title="",
        content="无关内容",
    )
    assert matched_keywords(item2, {"search": ["apple"]}) == []

    assert matched_keywords(item, {}) == []
    assert matched_keywords(item, None) == []

    item3 = NewsItem(
        source="test",
        url="https://example.com/c",
        title="Apple 发布会",
        content="本文未提及 MacBook",
    )
    assert matched_keywords(item3, {"search": ["macbook", "苹果"]}) == ["macbook"]
    print("\nmatched_keywords 测试通过!")


def test_build_canonical():
    item = NewsItem(
        source="test_source",
        url="https://example.com/article",
        title="测试标题",
        pub="2026-04-26T10:30:00",
        content="这是正文内容。",
        filtered=False,
        filter_reason=None,
    )
    msg = build_canonical(item, ["kw1", "kw2"])
    _assert_canonical_fields(msg, item, ["kw1", "kw2"])
    assert msg.published_at.ToJsonString() == "2026-04-26T10:30:00Z"

    item_no_pub = NewsItem(
        source="test_source",
        url="https://example.com/article2",
        title="无日期新闻",
        pub=None,
        content="正文",
        filtered=False,
        filter_reason=None,
    )
    msg2 = build_canonical(item_no_pub, [])
    _assert_canonical_fields(msg2, item_no_pub, [])
    delta = time.time() - msg2.published_at.ToSeconds()
    assert 0 <= delta < 10, "无 pub 时应填当前 UTC 时间"

    item_bad_pub = NewsItem(
        source="test_source",
        url="https://example.com/article3",
        title="坏日期",
        pub="not-a-date",
        content="正文",
        filtered=False,
        filter_reason=None,
    )
    msg3 = build_canonical(item_bad_pub, [])
    delta3 = time.time() - msg3.published_at.ToSeconds()
    assert 0 <= delta3 < 10, "pub 解析失败时应填当前 UTC 时间"

    item_empty = NewsItem(
        source="test_source",
        url="https://example.com/article4",
        title="空正文",
        content="   ",
        filtered=False,
        filter_reason=None,
    )
    with pytest.raises(ValueError):
        build_canonical(item_empty, [])

    print("\nbuild_canonical 测试通过!")


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
    stream = redis_conf["msgs_stream"]

    count1 = await publish_to_redis(config, [item1, item2, item3])
    assert count1 == 3, f"首次发布应为 3 条 (实际 {count1})"

    raws = _xrange_messages(stream, redis_conf)
    assert len(raws) == 3, f"XRANGE 应读到 3 条 (实际 {len(raws)})"
    for raw in raws:
        canonical.CanonicalMessage.FromString(raw)

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

    raws_after = _xrange_messages(stream, redis_conf)
    assert (
        len(raws_after) == 3
    ), f"XRANGE 应仍只有 3 条 (实际 {len(raws_after)})"

    client.delete(dedup_set)
    client.delete(stream)
    client.close()

    print(
        "\n去重测试通过! 首次发布 3 条, 重复发布 0 条, URL 标准化正确（去除尾部 / 和查询字符串）"
    )


def test_dedup():
    asyncio.run(_test_dedup())


async def _test_stream_readback():
    redis_conf = _make_redis_conf()
    dedup_set = redis_conf["dedup-set"]
    client = _redis_client(redis_conf)

    stream = redis_conf["msgs_stream"]
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

    count = await publish_to_redis(config, fake_items)
    assert count == len(fake_items)

    raws = _xrange_messages(stream, redis_conf)
    assert len(raws) == len(
        fake_items
    ), f"XRANGE 读数 {len(raws)} != 发布数 {len(fake_items)}"

    for i, raw in enumerate(raws):
        msg = canonical.CanonicalMessage.FromString(raw)
        _assert_canonical_fields(msg, fake_items[i], [])
        assert msg.published_at.ToSeconds() > 0, "published_at 非空"

    client.delete(dedup_set)
    client.delete(stream)
    client.close()

    print(
        f"\nStream 读回测试通过! 发布并读回 {len(fake_items)} 条 "
        f"canonical 消息到 stream '{stream}'"
    )


def test_stream_readback():
    asyncio.run(_test_stream_readback())


async def _test_e2e_fetch_format_publish():
    redis_conf = _make_redis_conf()
    dedup_set = redis_conf["dedup-set"]
    client = _redis_client(redis_conf)

    setup_logging("INFO")
    config = {"redis": redis_conf, "sources": [SOURCE_CONFIG]}
    stream = redis_conf["msgs_stream"]

    items = await _fetch_list_and_details(max_items=3)
    _apply_search_filter(items, ["Apple"])
    matched = unfiltered_items(items)

    assert len(matched) > 0, "搜索 'Apple' 应有匹配结果"

    count = await publish_to_redis(config, matched)
    assert count == len(matched)

    raws = _xrange_messages(stream, redis_conf)
    assert len(raws) == len(
        matched
    ), f"XRANGE 读数 {len(raws)} != 发布数 {len(matched)}"

    for i, raw in enumerate(raws):
        msg = canonical.CanonicalMessage.FromString(raw)
        _assert_canonical_fields(
            msg, matched[i], matched_keywords(matched[i], SOURCE_CONFIG["selectors"])
        )
        assert list(msg.tags) == ["Apple"], "tags 应含实际命中关键词"

    client.delete(dedup_set)
    client.delete(stream)
    client.close()

    print(
        f"\n完整 E2E 测试通过! 抓取 {len(matched)} 条新闻 -> 去重 -> 构造 "
        f"canonical -> XADD 到 Redis Stream '{stream}'"
    )


def test_e2e_fetch_format_publish():
    asyncio.run(_test_e2e_fetch_format_publish())
