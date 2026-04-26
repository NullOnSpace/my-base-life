# my-base-life

[![CI](https://github.com/NullOnSpace/my-base-life/actions/workflows/ci.yml/badge.svg)](https://github.com/NullOnSpace/my-base-life/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

基于 [base-life](https://github.com/NullOnSpace/base-life) 的新闻聚合与发布工具。从配置的新闻源抓取文章，按关键词过滤，格式化为 Markdown，并通过 Redis Pub/Sub 发布。

## 功能特性

- **多源抓取**：支持配置多个新闻源，自动解析列表页和详情页
- **关键词过滤**：按搜索词筛选匹配的文章
- **Markdown 格式化**：将新闻转换为结构化的 Markdown 文本
- **Redis Pub/Sub 发布**：将格式化后的新闻推送到指定频道
- **URL 去重**：基于 Redis Set 的去重机制，避免重复发布
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
channel = "news"             # 发布频道名称
dedup-set = "base-life:published"  # 去重集合名称
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

## 订阅新闻

使用 Redis 客户端订阅发布的新闻：

```python
import redis

client = redis.Redis(host="localhost", port=6379, db=0)
pubsub = client.pubsub()
pubsub.subscribe("news")

for message in pubsub.listen():
    if message["type"] == "message":
        print(message["data"].decode("utf-8"))
```

或使用 `redis-cli`：

```bash
redis-cli SUBSCRIBE news
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
├── main.py              # 主程序入口
├── config.example.toml  # 示例配置
├── config.toml          # 用户配置（gitignore）
├── test_e2e.py          # E2E 测试
├── tests/fixtures/      # 测试用 HTML 文件
├── pyproject.toml       # 项目配置
└── .github/workflows/   # CI 配置
```

## License

[MIT](LICENSE)
