# ADR-0003: SQLAlchemy + Alembic，SQLite 为 0.2.0 唯一生产数据库

- 状态：已接受（PostgreSQL 推迟到 0.3）
- 日期：2026-11-04
- 相关：ROADMAP-0.2.0 §6.4

## 背景

0.1.x 的 `Store` 是同步 `sqlite3` + 手写 `PRAGMA/ALTER TABLE` 迁移。它可靠、快、
零依赖，但没有版本链、没有 downgrade、没有 repository 边界，换数据库等于重写。

0.2.0 同时要引入多分享链接、API Keys、布局、命令块、推送订阅与审计哈希链。

## 决策

1. 引入 **SQLAlchemy 2.1 + Alembic 1.20**；
2. 新表（shares/api_keys/layouts/command_blocks/push_subscriptions/recordings/
   idempotency_keys）使用 SQLAlchemy
   ORM repository（`core/database.py`）；
3. 旧核心表（users/auth_sessions/term_sessions/instances/audit_logs）继续由
   `core/store.py` 用 SQLite 方言维护，schema 版本 v6；
4. Alembic 基线接住 v5，`0002_v6` 加列/建表，`0003_v6_complete` 补
   recordings/idempotency/last_seq 并导入旧分享，`0004_session_exit_code` 补
   退出码；启动时自动升级，并提供
   `wsctl migrate --check/--apply`；
5. **0.2.0 不接受任何非空 `database_url`**：校验器在启动时明确报错；数据库固定为
   `data_dir/wsctl.db`。

## 为什么推迟 PostgreSQL

把一半表放在 SQLAlchemy、另一半留在 `sqlite3` 方言上，会产生两个数据库、两套事务、
两套备份语义。那比「暂不支持」更糟。完整的 PostgreSQL 支持需要先把 Store 的 40+
个查询迁到 repository，并用真实 PostgreSQL 跑迁移与并发测试；这应作为一个独立
里程碑，而不是 0.2.0 的尾项。

因此在 0.2.0：

- `database_url` 必须为空；任何非空值会在启动时明确报错；
- 文档与 ADR 明确写出 0.3 计划；
- 数据层已经是 SQLAlchemy，后续迁移不需要再次更换技术栈。

## 后果

- SQLite 仍是单文件、WAL、在线备份；
- Alembic 版本链可审计、可 downgrade；
- 新增表可以用 ORM 单测；
- 0.3 的 PostgreSQL 工作被清晰隔离，不会以「半支持」形式泄给用户。

## 迁移

- 启动时自动 `upgrade head`；
- 升级前仍要求 `wsctl backup`；
- `wsctl migrate --check` 只读；
- 回退必须 `wsctl restore`，旧版本不能直接读 v6 库。
