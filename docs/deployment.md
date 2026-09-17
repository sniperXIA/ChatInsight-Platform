# ChatInsight Platform 部署与运维指南

## 1. 部署架构建议

ChatInsight Platform 采用轻量高效的单机/集群就绪架构：
- **Web API / 任务调度**：基于 Python 3.11+ / FastAPI，使用 Uvicorn 或 Gunicorn 多 Worker 部署。
- **存储层**：默认使用 SQLite 3（开启 WAL 模式、Busy Timeout 30s、Synchronous Normal），读写并发能力极佳，支持数十万条群聊消息与媒体索引毫秒级响应；如需大规模分布式部署，可通过 SQLAlchemy 异步连接池无缝切换至 PostgreSQL 15+。
- **多模态与 AI 网关**：通过 `ModelGateway` 统一路由至 OpenRouter 或自建 VLLM / Ollama 节点。

---

## 2. 生产环境配置清单

### 2.1 环境变量配置 (`.env`)

```ini
# 服务基础配置
APP_ENV=production
DEBUG=false
WORKSPACE_NAME=LiberLive User Community

# 数据库配置 (默认使用 SQLite 异步 WAL)
DATABASE_URL=sqlite+aiosqlite:///chatinsight.db

# OpenRouter / 模型网关
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxx
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
DEFAULT_VISION_MODEL=qwen/qwen-2.5-vl-72b-instruct
DEFAULT_TEXT_MODEL=deepseek/deepseek-chat

# 多模态媒体配置
FFMPEG_PATH=ffmpeg
FFPROBE_PATH=ffprobe
MEDIA_DERIVED_CACHE_DIR=./data/cache/previews

# ABC User Feedback 同步配置
ABC_FEEDBACK_API_URL=http://localhost:8080
ABC_FEEDBACK_API_KEY=optional_token_if_secured
```

---

## 3. 运维与定时任务 (Cron / Scheduled Jobs)

生产环境下建议配置定时任务定期增量扫描并自动聚合：

```bash
# 每晚 02:00 自动扫描增量社群聊天记录并执行全链路流水线
0 2 * * * cd /opt/chatinsight-platform && /opt/venv/bin/python cli.py pipeline --path "/data/wechat_exports" >> /var/log/chatinsight_cron.log 2>&1
```

---

## 4. 数据备份与恢复

SQLite 开启 WAL 模式下，直接复制主 `.db` 文件及 `-wal`, `-shm` 文件即可完成热备份：
```bash
sqlite3 chatinsight.db ".backup 'chatinsight_backup_$(date +%Y%m%d).db'"
```

---

## 5. 性能与容量规划

| 数据指标 | 单机支持容量 | 响应时延 | 备注 |
|---|---|---|---|
| 聊天消息量 | 5,000,000 条 | 检索 < 35ms | 建立全文与时间序列索引 |
| 媒体资产库 | 200,000 张图/视频 | 查询 < 20ms | 派生预览图本地缩略存储 |
| 聚类主题库 | 50,000 个 Topic | 两阶段聚类 < 150ms | 候选召回 + LLM 判定 |
| LLM 缓存率 | > 75% 缓存命中 | 0 计费 / < 5ms | 基于双重哈希去重缓存 |
