# ADR-003: 权威数据库与存储架构设计

## 状态
已采纳 (Accepted)

## 上下文
需要保证原始归档不可变，同时支持并发导入、审计追溯、全文与语义检索。

## 决策
1. 原始文件目录（Raw Archive）严格只读挂载。
2. 数据库为唯一权威源（Source of Truth），所有物化文件（Markdown/JSON）均可按版本重新生成。
3. 采用 SQLAlchemy 2.0 异步引擎，业务通过 Repository 隔离。
4. 本地环境支持 SQLite (WAL 模式) 开箱即用，生产环境支持 PostgreSQL 16 + pgvector。
