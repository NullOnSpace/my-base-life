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

from push.v1 import canonical_pb2 as canonical  # noqa: E402

__all__ = ["canonical", "CANONICAL_MSG_GEN_PATH"]

CANONICAL_MSG_GEN_PATH = str(_GEN)
