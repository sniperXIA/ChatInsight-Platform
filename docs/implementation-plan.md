# ChatInsight 研发实施计划 (Implementation Plan)

## 1. 总体目标
实现本地多模态用户反馈分析、证据知识研究与 ABC User Feedback 运营工作台集成的全流程闭环系统。

## 2. 阶段划分与实施路线

### Phase 0: 数据契约与样本审计 (当前阶段)
- [x] 样本结构审计与编码兼容性验证（已验证 `D:\玩家群聊天信息2` 历史与最新数据）
- [x] 建立 JSON Schema 与 Pydantic 数据契约 (`batch_manifest`, `messages`, `media_manifest`, `precheck_report`)
- [x] 开发高兼容性旧版 TXT 解析器（支持多行消息、中文冒号、表情、引用标记、未归属异常行捕捉）
- [x] 开发多模态媒体关联与歧义候选评估器（精确文件名匹配置信度 1.0，`[图片]` 歧义占位候选推测与人工确认标记）
- [x] 建立数据质量与预检报告引擎
- [x] 编写单元测试与脱敏样本回归测试

### Phase 1: Context Core 与证据浏览 (场景故事 1 完整闭环)
- [x] 权威数据库架构设计与 Repository 封装（支持 SQLite WAL 本地开发与 PostgreSQL + pgvector 部署）
- [x] 增量导入引擎（幂等写入、Source Record Hash、零重复）
- [x] 用户隐私与稳定匿名化标签生成 (HMAC-SHA256)
- [x] 证据与会话时间线浏览 API (`/api/v1/conversations`, `/api/v1/conversations/{id}/timeline`, `/api/v1/media/{id}`)
- [x] 媒体关系人工修复与审计接口 (`/api/v1/media-links/{id}/confirm`, `reject`)
- [x] 集成测试与真实数据全流程验证

### Phase 2: 多模态富化 (多模态解析)
- [ ] Model Gateway (OCR, VLM, ASR, Embedding, LLM 适配器)
- [ ] AnalysisRun 缓存与成本追踪
- [ ] 图片 OCR 与 UI/错误码视觉理解
- [ ] 视频 ASR 分段、关键帧提取、视觉摘要与时间线对齐
- [ ] 多模态 Context Packet 构建

### Phase 3: 对话事件 Episode 还原与 Insight 提炼
- [ ] Episode 边界切分算法（时间窗、引用图、语义相似度、技术支持响应）
- [ ] Episode 人工合并、拆分、移动消息与边界锁定
- [ ] Insight 结构化提取与字段级 Claim / Evidence 绑定
- [ ] 事实状态与产品功能目录规则校验
- [ ] 三栏式洞察审核后台与人工锁定保护

### Phase 4: 知识库、聚类与 ABC User Feedback 同步
- [ ] Entity / Relation / Claim / Evidence 图谱持久化
- [ ] 混合检索 (词法 + 向量 + 图扩展)
- [ ] Insight -> ABC Feedback 幂等投影
- [ ] Cluster -> ABC Issue 投影与双向关联
- [ ] Webhook 接收与 Inbox/Outbox 异步去重
- [ ] ABC 运营状态同步与冲突对账 (Reconciliation)

### Phase 5: 趋势分析、修复验证与产品研究助手
- [ ] 跨用户/跨群问题聚类 (Pairwise Judge)
- [ ] 独立用户数统计与 7日/28日 趋势引擎
- [ ] 14 天修复观察期与疑似回归检测
- [ ] 产品研究工作区 (Research Session & Note)
- [ ] 100% 证据引用校验与报告物化导出

### Phase 6: 评估、隐私、稳定性与 Windows 部署
- [ ] Golden Dataset 门禁测试
- [ ] 隐私脱敏与级联软删除
- [ ] 系统审计与备份恢复
- [ ] Windows 一键脚本 (`doctor.ps1`, `start.ps1` 等)
