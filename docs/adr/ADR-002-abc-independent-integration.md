# ADR-002: ABC User Feedback 独立集成架构决策

## 状态
已采纳 (Accepted)

## 上下文
ABC User Feedback 具备成熟的反馈管理、Issue 流转、看板和研发协作功能。

## 决策
1. ABC 作为独立服务运行，ChatInsight 仅通过其公开 REST API 与 Webhook 双向集成。
2. 严禁 ChatInsight 直接读写 ABC 的 MySQL 数据库。
3. 严格区分 ChatInsight 权威的事实状态（Factual State）与 ABC 运营状态（Operational Status）。
4. 使用 Outbox/Inbox 保证至少一次投递与幂等处理。
