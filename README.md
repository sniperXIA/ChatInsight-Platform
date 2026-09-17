# ChatInsight Platform (用户反馈洞察系统)

> **面向微信社群导出的多模态用户反馈洞察、事实性核验、智能知识库与 VoC 决策平台**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/Framework-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![SQLite WAL](https://img.shields.io/badge/Database-SQLite%20Async%20WAL-003B57.svg)](https://www.sqlite.org/)
[![OpenRouter](https://img.shields.io/badge/AI%20Gateway-OpenRouter%20%28Qwen--VL%20%7C%20DeepSeek%29-purple.svg)](https://openrouter.ai/)
[![Tests](https://img.shields.io/badge/Tests-32%20Passed%20100%25-brightgreen.svg)]()

---

## 🌟 系统核心价值

在硬件产品（如 **LiberLive C2 智能无弦吉他**）与软件生态的社群运营中，用户每天在微信群产生海量的文字答疑、硬件拍照求助、短视频报障与功能建议。传统人工收集或简单 RAG 存在以下痛点：
1. **微信导出混乱**：非结构化 TXT 格式脆弱、系统提示词混杂、表情与图片时间戳脱节。
2. **多模态割裂**：用户发图或发视频问“这怎么回事？”，文本与图片 OCR/界面状态无法关联。
3. **大模型幻觉与误判**：大模型容易把客服的“已知/收到”误判为“已修复”，把偶发咨询误判为产品严重缺陷。
4. **同类反馈海量重复**：多群多人反复提同一问题，缺乏两阶段智能去重与知识库沉淀。
5. **外部系统脱节**：洞察无法自动化结构化同步至企业级工单系统（如 ABC User Feedback）。

**ChatInsight Platform** 采用 **上下文核心（Context Core）+ 独立集成** 架构，完整实现了从原始聊天记录导入、多模态图文解析、对话智能切分、Claim 证据链事实核验、两阶段聚类去重、ABC 契约同步到混合检索与研究助手可追溯问答的端到端全链路闭环。

---

## 🏗️ 总体架构图

```
+-----------------------------------------------------------------------------------+
|                        ChatInsight Platform 架构全景                              |
+-----------------------------------------------------------------------------------+
                                        |
  [ 原始微信社群文件/图片/视频 (D:/玩家群聊天信息2) ]
                                        |
                                        v
+-----------------------------------------------------------------------------------+
| 1. 扫描预检与幂等导入 (packages/importers)                                        |
|    * DirectoryScanner: 阻断性异常预检 & 编码纠错                                  |
|    * LegacyTxtParser: 状态机容错解析、引用关系提取                                |
|    * MediaResolver: 显式/隐式图片视频消歧与关联                                   |
|    * BatchImporter: 增量 SHA256 幂等写入 SQLite WAL                              |
+-----------------------------------------------------------------------------------+
                                        |
                                        v
+-----------------------------------------------------------------------------------+
| 2. 多模态富化流水线 (packages/media_pipeline & model_gateway)                     |
|    * ImagePipeline: EXIF 自动校正 + Qwen-2.5-VL 视觉/OCR 提取                     |
|    * VideoPipeline: FFprobe 探测 + FFmpeg 关键帧采样 + 画面时间线合成             |
|    * AnalysisService: 输入与配置双重哈希审计缓存 (0 重复调用开销)                 |
+-----------------------------------------------------------------------------------+
                                        |
                                        v
+-----------------------------------------------------------------------------------+
| 3. 对话切分与 Claim 事实核验 (packages/insights)                                  |
|    * EpisodeSegmenter: 25 分钟时间窗口滑动切分 + Context Packet 上下文包组装      |
|    * InsightExtractor: 提炼 issue / feature / inquiry / praise 结构化洞察         |
|    * FactualChecker: 原子 Claim 证据链匹配 (chatinsight://... URI) 事实一致性打分|
|    * 严谨区分客服已知 (support_acknowledged) 与真正修复 (fix_confirmed)          |
+-----------------------------------------------------------------------------------+
                                        |
                                        v
+-----------------------------------------------------------------------------------+
| 4. 两阶段聚类与知识库沉淀 (packages/clustering & abc_sync)                        |
|    * Stage 1: CandidateRanker (产品模块划分 + n-gram 语义相似度召回)             |
|    * Stage 2: PairwiseJudge (大模型深度判定: SAME_ISSUE / SUB_ISSUE / DISTINCT)   |
|    * Topic 生命周期管理: 频次累加、用户数统计、动态摘要合并                       |
|    * ABCSyncService: 事务性 Outbox 事件分发与标准 ABCFeedbackPayload 契约推送     |
+-----------------------------------------------------------------------------------+
                                        |
                                        v
+-----------------------------------------------------------------------------------+
| 5. 混合检索、研究助手问答与 VoC 报表 (packages/search & analytics)                |
|    * HybridSearchEngine: 跨 Topic / Insight / Message / Media 的全文多维检索      |
|    * ResearchAssistant: 基于检索证据的严谨问答，强制绑定 [1], [2] Citation 引用  |
|    * ReportGenerator: 模块热度分布、严重级分布、Top 5 阻塞问题、一键 Markdown 导出|
+-----------------------------------------------------------------------------------+
```

---

## 🚀 快速启动

### 1. 环境准备
- **Python**: 3.11 或更高版本
- **FFmpeg & FFprobe**: 8.x+（已预装并加入 PATH）

### 2. 依赖安装
```bash
pip install -r requirements.txt
```

### 3. 环境配置 (`.env`)
```bash
# 复制环境配置模板
cp .env.example .env

# 配置 OpenRouter 密钥与模型
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxx
DEFAULT_VISION_MODEL=qwen/qwen-2.5-vl-72b-instruct
DEFAULT_TEXT_MODEL=deepseek/deepseek-chat
```

### 4. 运行全套自动化测试
```bash
python -m pytest tests/ -v
```
*(包含 32 个完整的单元测试、真实微信聊天样本测试、真实大模型 API 真实调用测试与端到端全链路验收测试，100% PASS)*

---

## 🛠️ CLI 命令行操作指南

ChatInsight 提供了功能完备的命令行工具 `cli.py`：

### 1. 一键全自动全链路流水线
```bash
python cli.py pipeline --path "D:/玩家群聊天信息2" --mock
```

### 2. 分步骤独立执行

| 命令 | 描述 | 示例 |
|---|---|---|
| `scan` | 预检扫描聊天目录与媒体文件 | `python cli.py scan -p "D:/玩家群聊天信息2"` |
| `import` | 正式导入聊天记录与媒体资产 | `python cli.py import -p "D:/玩家群聊天信息2"` |
| `analyze-media` | 触发图片/视频多模态解析 | `python cli.py analyze-media --limit 5` |
| `segment-episodes`| 执行话题切分与上下文包组装 | `python cli.py segment-episodes --limit 5` |
| `extract-insights`| 提炼洞察与 Claim 事实核验 | `python cli.py extract-insights --limit 5` |
| `cluster-insights`| 洞察两阶段聚类与主题沉淀 | `python cli.py cluster-insights --limit 10` |
| `sync-abc` | 推送主题至 ABC 平台 | `python cli.py sync-abc` |
| `search` | 跨多模态与知识库混合检索 | `python cli.py search -q "音色卡"` |
| `ask-assistant` | 向研究助手提问洞察问题 | `python cli.py ask-assistant --question "扩展音色卡的主要问题是什么？"` |
| `generate-report` | 生成并导出业务洞察报告 | `python cli.py generate-report --output "voc_report.md"` |

---

## 🌐 REST API 接口清单

启动 FastAPI 服务：
```bash
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload
```
交互式 API 文档地址：`http://localhost:8000/docs`

| 模块 | 方法 | 路由 | 功能说明 |
|---|---|---|---|
| **Health** | `GET` | `/api/v1/health` | 服务健康与数据库连通性检查 |
| **Data Sources**| `POST` | `/api/v1/source-roots/scan` | 触发目录扫描与生成预检报告 |
| **Imports** | `POST` | `/api/v1/imports/start` | 启动批次导入与异步解析 |
| **Conversations**| `GET`| `/api/v1/conversations` | 分页查询群聊会话列表 |
| **Media** | `POST` | `/api/v1/media/{id}/analyze` | 触发图片/视频多模态解析 |
| **Media** | `GET` | `/api/v1/media/{id}/enrichments` | 获取媒体 OCR 与视觉摘要详情 |
| **Episodes** | `POST` | `/api/v1/conversations/{id}/segment` | 对群聊执行话题切分 |
| **Episodes** | `GET` | `/api/v1/episodes/{id}/context-packet`| 获取 Episode 完整多模态上下文包 |
| **Insights** | `POST` | `/api/v1/episodes/{id}/extract` | 提取结构化洞察与 Claim 证据链 |
| **Insights** | `GET` | `/api/v1/insights` | 多维筛选洞察列表（状态/模块/严重级）|
| **Insights** | `POST` | `/api/v1/insights/{id}/review` | 人工审核（Approve / Reject / Override）|
| **Topics** | `POST` | `/api/v1/insights/{id}/cluster` | 触发单条洞察两阶段智能聚类归并 |
| **Topics** | `GET` | `/api/v1/topics` | 查询知识库主题列表（按热度频次排序）|
| **Topics** | `POST` | `/api/v1/topics/merge` | 人工手动合并主题 A 至主题 B |
| **ABC Sync** | `POST` | `/api/v1/abc/topics/{id}/sync` | 单条主题推送同步至 ABC 系统 |
| **ABC Sync** | `POST` | `/api/v1/abc/sync-batch` | 批量推送待同步主题 |
| **Search** | `POST` | `/api/v1/search/hybrid` | 跨知识库与消息的全文混合检索 |
| **Assistant** | `POST` | `/api/v1/assistant/ask` | 研究助手带证据链 Citation 问答 |
| **Analytics** | `GET` | `/api/v1/analytics/overview` | 全平台核心数据指标看板 |
| **Analytics** | `POST` | `/api/v1/analytics/reports/generate` | 生成结构化 VoC 周期报告 |
| **Analytics** | `GET` | `/api/v1/analytics/reports/export-markdown` | 导出 Markdown 格式洞察分析周报 |

---

## 🔒 隐私与事实性保障

1. **确定性脱敏**：群聊昵称采用 HMAC-SHA256 结合空间密钥单向哈希生成匿名代称（如 `玩家_4f8a`），原始微信号与敏感凭据绝对隔离。
2. **防幻觉证据锚点**：每个 Claim 主张均强绑定形如 `chatinsight://conv/{id}/msg_{mid}#text` 或 `chatinsight://media/{id}#ocr` 的 URI，事实校验器（`FactualChecker`）实时对齐原文，无事实依据的主张自动标红拦截。
3. **审计留痕与缓存**：所有 LLM/VLM 分析记录持久化至 `analysis_run` 表，基于 `(input_hash, config_hash)` 实现零重复计费调用；人工审核修正同步写入 `audit_event` 审计日志表。
