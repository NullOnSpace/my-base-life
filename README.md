# my-base-life

[![CI](https://github.com/NullOnSpace/my-base-life/actions/workflows/ci.yml/badge.svg)](https://github.com/NullOnSpace/my-base-life/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

基于 [base-life](https://github.com/NullOnSpace/base-life) 的新闻聚合与发布工具（消息推送分层架构的 **A 层**信源接入，总设计见 `~/rust-projects/push-architecture/DESIGN.md`）。从配置的新闻源抓取文章，按关键词过滤（D2：选题属 A），构造 [canonical-msg](~/rust-projects/push-architecture/canonical-msg) 仓库定义的 `push.v1` canonical 消息，通过 Redis Stream `XADD` 发布到 `stream:msgs:in`。

## 功能特性

- **多源抓取**：支持配置多个新闻源，自动解析列表页和详情页
- **关键词过滤**：按搜索词筛选匹配的文章（命中词写入 `tags`，可观测、可迁移）
- **canonical 消息**：输出 push.v1 CanonicalMessage（protobuf bytes），不做终端格式化（Markdown 渲染移交 render-hub）
- **Redis Stream 发布**：将 canonical 消息 `XADD` 到 `stream:msgs:in`（entry 单 field `data`，契约见 streams-lib `DATA_FIELD`）
- **URL 去重**：基于 Redis Set 的去重机制，**只记已发布 URL**，避免重复发布
- **URL 标准化**：自动去除查询字符串、片段标识符和尾部斜杠

## 依赖

- Python 3.12+
- Redis 服务器
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/NullOnSpace/my-base-life.git
cd my-base-life
```

### 2. 安装依赖

```bash
uv sync
```

### 3. 配置

复制示例配置并编辑：

```bash
cp config.example.toml config.toml
```

编辑 `config.toml` 配置你的新闻源和 Redis 连接信息。

### 4. 运行

```bash
uv run my-base-life config.toml
```

或直接运行（默认读取 `config.toml`）：

```bash
uv run my-base-life
```

## 配置说明

配置文件使用 TOML 格式，包含以下部分：

### 日志配置

```toml
[logging]
level = "INFO"  # DEBUG, INFO, WARNING, ERROR
```

### Redis 配置

```toml
[redis]
host = "localhost"           # Redis 服务器地址
port = 6379                  # Redis 端口
db = 0                       # Redis 数据库编号
password = ""                # 密码（留空表示无密码）
msgs_stream = "stream:msgs:in"  # push.v1 canonical 消息发布目标（XADD；field "data" 契约见 streams-lib）
dedup-set = "base-life:published"  # 去重集合（只记已发布 URL）
```

### 新闻源配置

每个新闻源使用 `[[sources]]` 定义：

```toml
[[sources]]
name = "subscribed"
url = "https://example.net/archive/"

[sources.selectors]
list_selector = "div.archive p > a"  # 列表页链接选择器
title = "h1"                          # 标题选择器
pub = "h6.dateline"                   # 发布时间选择器
pub-format = "%d %B %Y"               # 发布时间格式（strptime 指令）
content = "div.article"               # 正文选择器
search = ["MacBook Neo"]              # 搜索关键词（可选）
```

#### `pub-format` 支持的 strptime 指令

| 指令 | 含义 | 示例 |
|------|------|------|
| `%Y` | 4 位年份 | 2026 |
| `%y` | 2 位年份 | 26 |
| `%m` | 月份 | 01-12 |
| `%d` | 日期 | 01-31 |
| `%H` | 小时（24h） | 00-23 |
| `%M` | 分钟 | 00-59 |
| `%S` | 秒 | 00-60 |
| `%B` | 完整月份名 | January |
| `%b` | 缩写月份名 | Jan |
| `%p` | AM/PM | AM, PM |

## 消息契约（canonical-msg）

发布载荷为 `push.v1.CanonicalMessage`（protobuf），契约单一事实源在 `~/rust-projects/push-architecture/canonical-msg` 仓库（proto 定义 + 双语言生成代码 + 契约测试）。Python 侧由 `canonical_msg.py` 接入其 `gen/python` 生成代码，路径解析顺序：

1. 环境变量 `CANONICAL_MSG_GEN_PATH`（绝对路径，指向含 `push/` 包的目录）
2. 默认：canonical-msg checkout 的 `gen/python`（本机开发布局）

`gen/` 的任何改动必须经 canonical-msg 的 `scripts/gen_python.sh` 再生成，禁止手改。

## 读取新闻

发布的载荷是 canonical 消息（protobuf bytes），生产消费归 push-core（consumer group + pending 兜底）。本仓仅产出，读回验证用 Redis 客户端 `XRANGE` 并解码：

```python
import sys
sys.path.insert(0, "/path/to/canonical-msg/gen/python")  # 或设 CANONICAL_MSG_GEN_PATH
import redis
from push.v1 import canonical_pb2

client = redis.Redis(host="localhost", port=6379, db=0)
for _entry_id, fields in client.xrange("stream:msgs:in", min="-", max="+"):
    msg = canonical_pb2.CanonicalMessage.FromString(fields[b"data"])
    print(msg.title, msg.body_markdown)
```

## 开发

### 运行测试

测试需要本地 Redis 服务：

```bash
uv run pytest test_e2e.py -v
```

### 项目结构

```
my-base-life/
├── main.py              # 主程序入口（抓取→过滤→canonical 构造→Redis 发布）
├── canonical_msg.py     # push.v1 canonical 构造（build_canonical / matched_keywords）
├── config.example.toml  # 示例配置
├── config.toml          # 用户配置（gitignore）
├── test_e2e.py          # E2E 测试
├── tests/fixtures/      # 测试用 HTML 文件
├── pyproject.toml       # 项目配置
└── .github/workflows/   # CI 配置
```

## License

[MIT](LICENSE)
