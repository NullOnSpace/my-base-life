"""push.v1 canonical 消息构造（my-base-life A 层信源接入适配）。

契约单一事实源（SSOT）：~/rust-projects/push-architecture/canonical-msg 仓库。
生成代码（gen/python，含 push/v1 pb2 模块）路径解析顺序：
1. 环境变量 CANONICAL_MSG_GEN_PATH（绝对路径，指向含 push/ 包的目录）
2. 默认：canonical-msg checkout 的 gen/python（本机开发布局）
"""

import os
import sys
from pathlib import Path

_GEN = Path(
    os.environ.get("CANONICAL_MSG_GEN_PATH")
    or Path(__file__).resolve().parent.parent.parent
    / "rust-projects"
    / "push-architecture"
    / "canonical-msg"
    / "gen"
    / "python"
)
sys.path.insert(0, str(_GEN))

from datetime import datetime, timezone  # noqa: E402

from google.protobuf import json_format  # noqa: E402
from ulid import ULID  # noqa: E402

from push.v1 import canonical_pb2 as canonical  # noqa: E402

__all__ = [
    "canonical",
    "CANONICAL_MSG_GEN_PATH",
    "build_canonical",
    "matched_keywords",
    "protojson",
]

CANONICAL_MSG_GEN_PATH = str(_GEN)


def protojson(msg: canonical.CanonicalMessage) -> str:
    return json_format.MessageToJson(
        msg, indent=0, ensure_ascii=False, sort_keys=True
    )


def matched_keywords(item, selectors: dict | None) -> list[str]:
    """返回实际命中的 search 词（保持配置词序、去重）。

    匹配语义与 base-life `_apply_search_filter` 一致（大小写不敏感子串，
    搜索文本 = 标题 + " " + 正文）；词表只读 config selectors，与 base-life 同源。
    search 为空时返回 []。
    """
    terms = (selectors or {}).get("search", []) or []
    if not terms:
        return []
    parts = [v for v in (item.title, item.content) if v]
    text = " ".join(parts).lower()
    seen: set[str] = set()
    matched: list[str] = []
    for term in terms:
        lowered = term.lower()
        if lowered in text and lowered not in seen:
            seen.add(lowered)
            matched.append(term)
    return matched


def _parse_published(pub: str | None) -> datetime:
    if pub:
        try:
            return datetime.fromisoformat(pub)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def build_canonical(item, matched: list[str]) -> canonical.CanonicalMessage:
    """按 DESIGN §5.3 字段映射构造 CanonicalMessage（kind=NEWS）。

    守卫（不变量 #1）：body_markdown 去空白后为空则抛 ValueError，
    调用方须记 ERROR 跳过（不得发布空正文，也不得写入 dedup set）。
    """
    body_markdown = (item.content or "").strip()
    if not body_markdown:
        raise ValueError(f"正文为空，拒绝发布: {item.url}")

    from main import normalize_url  # 延迟导入避免循环依赖（内部复用）

    msg = canonical.CanonicalMessage()
    msg.id = str(ULID())
    msg.source.kind = canonical.SOURCE_KIND_CRAWLER
    msg.source.id = f"base-life:{item.source}"
    msg.source.display_name = item.source
    msg.dedup_key = f"base-life:{item.source}:{normalize_url(item.url)}"
    msg.kind = canonical.MSG_KIND_NEWS
    msg.title = item.title or ""
    msg.body_markdown = body_markdown
    msg.body_text = body_markdown
    msg.links.add(rel="original", url=item.url)
    msg.tags.extend(matched)
    msg.published_at.FromDatetime(_parse_published(item.pub))
    return msg
