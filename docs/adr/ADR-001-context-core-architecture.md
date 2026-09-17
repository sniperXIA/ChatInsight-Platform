# ADR-001: 自研 Context Core 架构决策

## 状态
已采纳 (Accepted)

## 上下文
需要解决微信群聊大量多模态数据、长周期问题跨群讨论、事实与工作流状态分离的业务诉求。MyContext 提供了优秀的本地上下文解耦和 Evidence-first 架构思想，但其处于 Developer Preview 阶段，且领域模型面向个人而非团队 VoC。

## 决策
1. 借鉴 MyContext 的核心架构思想：数据源与消费解耦、增量采集检查点、数据库为权威源、Markdown 为物化产物、先有证据再有答案。
2. 自研 ChatInsight Context Core 领域模型（Message, Episode, Insight, Claim, Evidence, Cluster, Research Note），不强依赖 MyContext 源码。
