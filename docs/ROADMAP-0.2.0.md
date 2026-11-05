# wsctl 0.2.0 迭代方案

> 状态：**已实施**（2026-11-04）  
> 基线：v0.1.30（commit `c6f1d02`）  
> 日期：2026-11-04  
> 目标版本：0.2.0  
> 兼容策略：**允许破坏性变更，不承诺与 0.1.x 双向兼容**  
> 文档定位：0.2.0 立项、架构决策、排期与验收的唯一事实源

## 实施结果（2026-11-04 / 第二轮审查 2026-11-05）

- 前端已重写为 `web/`（Vue 3 + TypeScript + Vite 8 + Pinia + Naive UI + xterm 6），
  构建产物在 `src/wsctl/static`；旧 `app.js` / `app.css` / `vendor/` 已删除。
- `/ws/v2` 已上线：seq/ack/resume/gap/snapshot，`SessionActor` + 带序号的
  `Scrollback`、tmux/pyte/raw 快照、OSC 133/633 命令块。
- `/api/v2` 已上线，旧 `/api` 作为一版兼容别名保留（带 `Deprecation` 头）。
- SQLAlchemy 2.1 + Alembic 已接入；schema v6（head `0004_session_exit_code`）；
  `wsctl migrate --check/--apply`，迁移前自动做 SQLite 一致性备份。
- 配置 v2 分节 TOML + `"12h"`/`"8MiB"` + `wsctl config migrate`；
  `config set server.port` 与 `[auth] required` 等分节写法均受支持。
- 可观测性：structlog、prometheus-client 直方图、OpenTelemetry（可选，含
  WS attach/replay/snapshot、PTY read、audit flush span）、`/readyz`、审计哈希链。
- 分享 v2（多链接/read/write/control/在线观众）、API Keys、布局同步、Web Push、
  主题导入、Shell Integration 安装器。
- API 契约：`/api/v2/auth/*`、RFC 9457 Problem Details、游标分页、
  `Idempotency-Key`、`ETag`/`If-None-Match`、`GET/PUT /api/v2/config`。
- 前端：生成式 OpenAPI 客户端、TanStack Query、ESLint/Prettier、Vitest +
  Vue Test Utils、Playwright 跨浏览器/移动端/视觉回归/断网恢复。
- 测试：pytest 全量（718 passed / 8 skipped）、Vitest 34 passed、Playwright
  Chromium/WebKit/Firefox/移动端、`scripts/e2e` 全场景。
- 审查修复：精确续传不再先清屏；退出码持久化到 `term_sessions.exit_code`；
  SSE 事件按会话属主过滤；旧 `/ws` 也执行 API Key scope；并发
  `Idempotency-Key` 原子抢占；上传 no-overwrite 原子化；撤销/过期分享不再回放到
  已结束会话；后台标签页写入与 ACK 不再依赖 rAF。
- 第二轮全量 review 修复：续传游标与 ACK 游标分离（快照渲染字节不再计入偏移）；
  手动重新同步与直播输出在同一把会话锁内排序；分享缓存过期不再误踢观众；
  配置接口不回显 VAPID 私钥/webhook 凭证；Origin 校验加入 scheme；
  API 响应默认 `no-store`；数据库/WAL/配置/录制/备份统一 0600；
  重命名不再覆盖 `generation`/`snapshot_mode`；幂等键按用户隔离；
  Web Push 仅接受公网 HTTPS 端点；审计并发 flush 保持顺序；
  SSE 溢出主动断开以便重连；文件编辑器保留权限位；虚拟列表过滤后回到顶部。

### 与草案的差异

1. **PostgreSQL 推迟到 0.3**：旧核心 Store 仍是 SQLite 方言，半迁移会导致双数据库。
   `database_url` 只接受 SQLite，其他 URL 启动即报错。见 ADR-0003。
2. **旧 `/api`、`/ws` 保留一版**：草案写 410/426；实际选择“兼容别名 + Deprecation
   头”，让升级窗口内的脚本与已打开标签不中断。新代码只使用 v2。
3. **TypeScript 5.9 而非 7.0**：`vue-tsc` 3.3 尚不支持 TS 7 的 `lib/tsc` 导出；
   升到 TS 7 需要等工具链，不是产品决策。
4. **依赖取舍**：控制帧 JSON 使用 orjson；登录限速已改用 `limits`。
   `aiosqlite`/`msgspec`/`tenacity` 未引入：数据层是同步 SQLAlchemy + 工作线程，
   `aiosqlite` 没有调用点；控制帧用 orjson 已经满足吞吐；CLI 的重连退避是显式状态机，
   比通用重试更可控。`httpx` 仅在开发/测试中使用，CLI 运行时仍用标准库
   `urllib`，避免为一个单用户命令行再增加运行时依赖。

### 未在 0.2.0 交付（明确边界）

- PostgreSQL（0.3）；
- OIDC / WebAuthn / Passkeys（0.3）；
- tus 分片续传（5.7 已标注为 0.2.x 可选增强）；
- 微服务、Kubernetes Operator、多节点会话调度、命令级沙箱（非目标）；
- 协作"跟随主持人切换会话"（0.2.0 提供在线观众列表与"跟随输出"；跨会话跟随
  需要工作区级协作模型，列入 0.3）；
- WebKit 已在 CI 配置 `playwright install --with-deps webkit`，开发机因缺少系统库
  只本地验证了 Chromium / Firefox / 移动端。


---

## 0. 摘要

0.1.x 已经证明 wsctl 可以成为一个可靠的「单机 Web 终端」：会话与连接解耦、背压不踢观众、分享跨重启、tmux 恢复、审计与 RBAC 都已闭环，测试纪律也远高于同类个人项目。

但 0.1.30 也走到了当前架构的上限：

- 前端是一个 5887 行的 IIFE 单体，靠全局状态和字符串拼 DOM；
- 终端恢复依赖「原始字节回放 + SIGWINCH 逼应用重绘」；
- WebSocket 协议没有版本、序号、ACK、断点续传与屏幕快照；
- SQLite schema 靠手工 `PRAGMA` + `ALTER TABLE` 迁移；
- 配置是扁平 TOML + 人工维护的三档热更新集合；
- 指标、限速、审计、日志都是自研小实现；
- 59 条前端结构契约靠解析 `app.js` 源码字符串，前端一旦组件化就会全部失效；
- `dist/` 里仍残留 0.1.3 的旧构建产物。

0.2.0 不做「0.1.31 式」的渐进增强，而做一次平台换轨：

1. **网页终端 → 终端工作台**：Vue 3 + TypeScript + Vite 重写前端，保留并升级 xterm。
2. **字节回放 → 可恢复终端协议**：WebSocket v2，序号、ACK、断点续传、屏幕快照、精确 gap。
3. **自研基础设施 → 成熟工程组件**：SQLAlchemy 2 + Alembic、structlog、prometheus-client、OpenTelemetry、limits、msgspec/orjson、httpx/tenacity。

**一句话定位：**

> wsctl 0.2.0 = 单机部署的 **Web Terminal Workspace**。  
> 会话仍然独立于连接，但连接可以精确恢复；终端不只是能输入输出，而是有命令块、工作区布局、文件编辑、协作分享和可观测性的工作台。

---

## 1. 背景与现状

### 1.1 产品定位

wsctl 是面向单机部署的 Web 终端工作台：

- 单机部署，`pip install` 零编译；
- 一条命令把终端搬上浏览器；
- 一个服务托管 N 个会话；
- 会话独立于连接，断线重连恢复屏幕；
- 多用户 RBAC、审计、分享、录制、文件面板、可观测性；
- 可选 tmux 后端跨服务重启恢复；
- 可选 SSH 后端。

0.2.0 不改变这个定位，但要把「可用」提升到「可长期工作」。

### 1.2 代码与测试基线

| 项 | 现状 |
|---|---|
| 版本 | 0.1.30 |
| Python | `requires-python >= 3.11`，CI 覆盖 3.11 / 3.12 / 3.13 |
| 后端框架 | FastAPI + Starlette + Uvicorn |
| 前端 | 原生 JS IIFE + 内嵌 xterm.js 5.5.0，零构建 |
| 数据库 | SQLite，schema v5，手工幂等迁移 |
| 测试 | 约 50 个 browser 用例、59 条前端结构契约、12 个 e2e 场景，以及大量后端单测/集成测试 |
| 发布 | hatchling + PyPI Trusted Publishing + GitHub Release |
| 部署 | systemd / Docker / nginx / SO_REUSEPORT 滚动重启 |

### 1.3 现有架构

```text
Browser (xterm.js 5.5, vanilla JS)
  ├─ REST /api
  └─ WebSocket /ws（二进制终端字节 + JSON 控制）
        │
FastAPI / Uvicorn
  ├─ SessionManager
  │    └─ TermSession
  │         ├─ PTY (local / tmux / ssh)
  │         ├─ Scrollback（原始字节环形缓冲）
  │         ├─ clients[]（每客户端有界出站队列）
  │         └─ Recorder
  ├─ SQLite Store（同步 sqlite3 + 线程锁）
  ├─ AuditWriter（asyncio 队列 + 批量写）
  ├─ Metrics（自研 Prometheus 文本）
  ├─ RateLimiter（自研滑动窗口 / 令牌桶）
  └─ WebhookDispatcher
```

### 1.4 主要结构性瓶颈

#### A. 前端单体

`src/wsctl/static/app.js` 5887 行，包含：

- 登录、终端、标签、分屏、文件面板、分享、管理面板、设置、录制、回放、搜索、ZMODEM、快捷键、通知、Toast、弹窗、主题；
- 所有状态是模块级变量；
- DOM 通过 `innerHTML` / `createElement` 手工拼装；
- 没有类型系统、组件边界、依赖注入、单元测试；
- 59 条 `tests/test_ui_contracts.py` 通过正则解析源码字符串来保护设计决策。

这套结构在 0.1.x 是合理的「零构建」取舍，但继续扩展的边际成本已经很高。

#### B. 恢复模型是 workaround

当前恢复路径：

```text
PTY 输出 → Scrollback 原始字节 → attach 时回放 → 客户端 xterm 重建屏幕
                                      │
                                      ├─ 丢帧 → desync
                                      ├─ scrollback 淘汰头部 → 回放无头
                                      └─ TUI 增量绘制 → 屏幕花屏
```

`nudge_repaint()` 通过两次真实 SIGWINCH 让应用从自身模型重绘，是聪明的兜底，但它：

- 只对全屏 TUI 有效，行式 shell 不会重绘；
- 有 250ms 静默等待与 3s 放弃窗口；
- 期间 mute scrollback，可能吞掉真实输出；
- 无法告诉客户端「具体丢了哪一段」。

根因是协议没有序号、ACK、快照，服务端不知道客户端到底收到了哪里。

#### C. 协议没有版本与流控

当前协议：

- 二进制帧 = 原始终端字节；
- 文本帧 = JSON 控制；
- `attach` 时全量回放 scrollback；
- `resync` 时再全量回放；
- 慢客户端丢最旧帧，只发一个笼统的 `desync`；
- 没有协议版本协商、没有 `last_seq`、没有 ACK、没有 generation。

这导致：

- 重连只能「全量重放」，4 MiB scrollback 每次重连都重新传；
- 客户端无法知道丢失的准确区间；
- 服务端无法做精确的增量恢复；
- 旧客户端/新服务端无法安全共存。

#### D. 存储与配置的工程化不足

- `Store` 是同步 sqlite3 + 全局线程锁，schema 迁移靠 `PRAGMA table_info` + `ALTER TABLE`；
- 没有 migration 版本链、没有 downgrade、没有 repository 抽象；
- 配置是扁平 TOML，热更新三档集合手工维护；
- 新配置项必须同时改模型、三档集合、Web 编辑器、README、测试，容易漏；
- 测试与 SQL 细节强耦合，换存储成本高。

#### E. 观测与安全组件可维护性

自研 `metrics.py`、`ratelimit.py`、`audit.py`、`logging.py` 都能工作，但：

- 缺少标准 HELP/TYPE 生态、直方图、Exemplar；
- 限速没有多后端、没有标准存储；
- 日志没有 request_id / trace_id 关联；
- 没有 OpenTelemetry 链路；
- 审计没有篡改证据。

#### F. 发布与前端资产

- vendor 目录 932 KB，xterm 5.5.0，无 tree-shaking、无 source map；
- 前端无依赖锁、无 SBOM、无 SRI；
- `dist/` 仍是 0.1.3 的 wheel/sdist，发布产物需要清理并重建；
- Docker 镜像只构建 Python，不构建前端（因为前端零构建）。

### 1.5 必须继承的资产

0.2.0 可以推翻实现，但以下产品契约必须保留：

1. **会话独立于连接**：关闭标签只 detach，不 kill。
2. **慢观众丢帧不踢连接**：控制帧不丢，丢失必须报告。
3. **分享跨重启存活**：token 持久化，重命名不失效。
4. **tmux 跨重启恢复**：优雅关闭保留 tmux 会话，启动自动收养。
5. **安全边界**：同源校验、受信代理链、WS 连接帽、输入帧上限、账号生命周期、文件防穿越、分享撤销。
6. **审计闭环**：登录、会话、文件、管理操作全量记录。
7. **CLI 运维体验**：doctor、滚动重启、TLS 继承、实例跟随、配置热更新报错。
8. **测试方法论**：算术完成标记、突变体守卫、真实浏览器、真实进程 e2e、性能门槛。

---

## 2. 0.2.0 目标

### 2.1 产品目标

| 目标 | 说明 |
|---|---|
| **工作台** | 标签、分屏、命令面板、会话侧栏、文件编辑、状态栏，从「终端框」变成「工作区」 |
| **无损恢复** | 重连不白屏、不花屏；TUI 用快照恢复；精确知道丢了哪一段 |
| **命令块** | Shell Integration：命令、耗时、退出码、cwd、复制输出、重新运行 |
| **协作分享** | 多分享链接、只读/可写/可控制、在线观众、跟随模式 |
| **可维护** | 前端组件化、后端分层、存储迁移、配置类型化、观测标准化 |
| **可验证** | 协议契约、前端单测、跨浏览器、a11y、混沌、性能预算 |

### 2.2 技术目标

1. 前端：Vue 3 + TypeScript + Vite + Pinia，xterm 6，组件化，可单测。
2. 协议：WebSocket v2，seq / ack / resume / snapshot / gap，版本协商。
3. 运行时：SessionActor 串行化，ScrollbackRing 带字节序号，输出合批。
4. 存储：SQLAlchemy 2.1 + Alembic，SQLite（0.2.0 唯一支持；PostgreSQL 0.3）。
5. 配置：分节 TOML + 模型驱动热更新策略 + 自动迁移。
6. API：`/api/v2` + OpenAPI 3.1 + 生成式 TypeScript 客户端 + SSE。
7. 观测：prometheus-client + structlog + OpenTelemetry。
8. 安全：limits、API Keys、审计哈希链、CSP/SRI、SBOM/签名。
9. 测试：Vitest + Playwright + 协议 fuzz + 迁移测试 + 性能门槛。

### 2.3 非目标

0.2.0 明确不做：

- 微服务化、Kubernetes Operator、控制平面；
- 多节点会话调度（仍以单机/单数据目录为主）；
- 命令级沙箱隔离（wsctl 仍是 RCE 服务，按 SSH 对待）；
- 递归删除文件；
- 完整 IDE（只做终端工作台 + 轻量编辑器）；
- Office/PDF 文档预览（位图、文本、代码仍支持）；
- 默认开启的公网 Web Push（作为可选能力）；
- 与 0.1.x 客户端/配置/数据库双向兼容。

### 2.4 成功标准

0.2.0 发布时，以下必须同时成立：

- 0.1.30 的功能矩阵逐项通过（保留或明确改进）；
- 协议 v2 在断网、丢帧、服务重启、TUI、慢观众场景下无白屏/花屏；
- 前端核心路径有 Vitest + Playwright 覆盖；
- `pip install wsctl` 仍然零编译、开箱即用；
- 升级前自动备份，v5 数据库与 v1 配置可迁移；
- 性能门槛达标；
- 安全审查与跨浏览器测试通过。

---

## 3. 目标架构

```text
┌──────────────────────────── Browser ────────────────────────────┐
│ Vue 3.5 + TypeScript + Vite 8                                   │
│                                                                  │
│  Workspace                                                       │
│    ├─ TabBar / SplitPane / CommandPalette / SessionSidebar       │
│    ├─ TerminalPane（@xterm/xterm 6 + WebGL/Canvas/DOM）          │
│    ├─ FilePanel + CodeMirror 6 Editor                            │
│    └─ Settings / Share / Admin / Replay                          │
│                                                                  │
│  Pinia stores · OpenAPI generated client · IndexedDB cache       │
└───────┬──────────────────┬──────────────────┬───────────────────┘
        │ REST /api/v2     │ WS /ws/v2        │ SSE /api/v2/events
┌───────▼──────────────────▼──────────────────▼───────────────────┐
│ FastAPI 0.162 / Starlette 1.10 / Uvicorn 0.54 + uvloop           │
│                                                                  │
│  API Layer                                                       │
│    └─ Routers · Pydantic schemas · Problem Details · OpenAPI     │
│                                                                  │
│  Application Services                                            │
│    ├─ SessionService · ShareService · RecordingService           │
│    ├─ UserService · AuditService · ConfigService                 │
│    └─ FileService · LayoutService                                │
│                                                                  │
│  Domain                                                          │
│    ├─ SessionActor（每会话一个 mailbox）                          │
│    ├─ ProtocolCodec（seq / ack / resume / snapshot / gap）        │
│    ├─ ScrollbackRing（字节序号）                                  │
│    ├─ SnapshotProvider（pyte / tmux / raw）                       │
│    └─ CommandBlock / Share / Recording                           │
│                                                                  │
│  Infrastructure                                                  │
│    ├─ SQLAlchemy 2.1 + Alembic（SQLite；PostgreSQL 0.3）           │
│    ├─ PTY / tmux / ssh                                           │
│    ├─ limits（限速）                                             │
│    ├─ prometheus-client + OpenTelemetry                          │
│    ├─ structlog + orjson                                         │
│    └─ Audit hash chain                                           │
└───────┬──────────────────────────────────────────────────────────┘
        │
   SQLite（0.2.0 唯一支持；PostgreSQL 在 0.3）
```

### 3.1 关键架构决策

| 决策 | 结论 | 理由 |
|---|---|---|
| 是否换 Web 框架 | **不换，保留 FastAPI** | 瓶颈不在 ASGI 框架；换 Litestar 只增加风险 |
| 是否换前端框架 | **Vue 3 + TypeScript + Vite** | 组件化、类型、构建生态；中文社区成熟 |
| 是否用 React | 不推荐 | 团队/生态不是问题，Vue SFC 更贴近现有 DOM 结构 |
| 是否用 docking 库 | **先保留二叉树 + splitpanes** | 避免 docking 库接管 xterm DOM 生命周期 |
| 是否用 SQLAlchemy | **是** | 迁移、事务、repository、Postgres 可选 |
| 是否用 pyte | **受控使用** | TUI 快照；Sixel/不支持序列回退 |
| 是否引入微服务 | **否** | 单机定位；模块化单体足够 |
| 是否保留零构建 | **用户侧保留，开发者侧放弃** | wheel 预构建，开发者需要 Node/pnpm |

---

## 4. 技术栈与依赖升级

> 版本以 2026-11-04 最新稳定线为基线。最终以 `uv.lock` / `pnpm-lock.yaml` 锁定，并经 CI 矩阵验证。

### 4.1 后端

| 组件 | 0.1.30 | 0.2.0 目标 | 动作 |
|---|---|---|---|
| Python | >=3.11 | **>=3.12，主测 3.13** | 升级；CI 覆盖 3.12/3.13/3.14 |
| FastAPI | >=0.115 | **0.162.x** | 保留并升级 |
| Starlette | >=0.37 | **1.10.x** | 随 FastAPI 升级 |
| Uvicorn | >=0.30 | **0.54.x** | 保留默认服务器 |
| uvloop | 未显式 | **0.23.x** | POSIX 默认启用 |
| httptools | 未显式 | **0.9.x** | HTTP 解析加速 |
| Pydantic | >=2.7 | **2.14.x** | 类型化 API/配置 |
| pydantic-settings | >=2.3 | **2.16.x** | 配置 v2 |
| Typer | >=0.12 | **0.27.x** | CLI 保留 |
| Rich | >=13.7 | **15.x** | CLI 输出 |
| websockets | >=13 | **17.4.x** | CLI 客户端 |
| argon2-cffi | >=23.1 | **25.1.x** | 密码哈希 |
| python-multipart | >=0.0.9 | **0.0.33.x** | 上传 |
| pyotp | >=2.9 | **2.10.x** | TOTP |
| segno | >=1.6 | **1.6.6** | 二维码 |
| cryptography | >=42 | **50.x** | TLS |
| **SQLAlchemy** | 无 | **2.1.x** | 引入 ORM/Core/事务 |
| **Alembic** | 无 | **1.20.x** | 正式迁移 |
| **aiosqlite** | 无 | **0.22.x** | SQLite async |
| **asyncpg** | 无 | **0.2.0 未引入（0.3）** | PostgreSQL |
| **msgspec** | 无 | **0.22.x** | WS 控制帧/结构体 |
| **orjson** | 无 | **3.13.x** | JSON 加速 |
| **structlog** | 自研 JSON | **26.1.x** | 结构化日志 |
| **prometheus-client** | 自研 | **0.26.x** | 标准指标 |
| **OpenTelemetry** | 无 | **SDK 1.45.x + FastAPI instr.** | 链路追踪 |
| **limits** | 自研 | **5.8.x** | 限速 |
| **httpx** | urllib | **0.28.x** | CLI HTTP |
| **tenacity** | 无 | **9.2.x** | 重试 |
| **watchfiles** | mtime 轮询 | **1.3.x** | 配置热更新 |
| **psutil** | /proc + ps | **7.2.x** | 进程/资源 |
| **pyte** | 无 | **0.8.x** | 终端快照 |
| **sse-starlette** | 无 | **3.6.x** | SSE |
| pytest | >=8.3 | **9.2.x** | 测试 |
| ruff | >=0.6 | **0.17.x** | Lint |
| mypy | >=1.11 | **2.4.x** | 类型 |
| Hypothesis | 无 | **6.170.x** | 协议/状态机 fuzz |
| Schemathesis | 无 | **4.33.x** | OpenAPI 契约 |
| pytest-benchmark | 无 | **5.3.x** | 性能基准 |
| pytest-xdist | 无 | **3.8.x** | 并行测试 |

**可选/后续：**

- `granian 2.8.x`：作为实验服务器，不替代默认 Uvicorn；
- `dishka 1.10.x`：如果 service 生命周期复杂，再引入 DI；
- `authlib 1.9.x` / `webauthn 3.0.x`：OIDC / Passkeys，0.2.x 或 0.3；
- `sentry-sdk 2.73.x`：可选错误上报；
- `zstandard 0.25.x`：如需压缩归档。

### 4.2 前端

| 组件 | 0.1.30 | 0.2.0 目标 |
|---|---|---|
| 框架 | 原生 JS IIFE | **Vue 3.5.x + TypeScript 7.x** |
| 构建 | 无 | **Vite 8.x + pnpm + Node 24 LTS** |
| 状态 | 全局变量 | **Pinia 4.x + TanStack Query（Vue）** |
| UI 组件 | 自研 CSS/DOM | **Naive UI 2.45.x + Tailwind CSS 4.x** |
| 终端 | xterm.js 5.5.0 | **@xterm/xterm 6.0.x** |
| Fit | addon-fit 0.10.0 | **0.11.x** |
| WebGL | addon-webgl 0.18.0 | **0.19.x** |
| Canvas | addon-canvas 0.7.0 | **0.7.x** |
| Search | addon-search 0.16.0 | **0.16.x** |
| Unicode | addon-unicode11 0.8.0 | **0.9.x** |
| Web Links | addon-web-links 0.11.0 | **0.12.x** |
| Image/Sixel | addon-image 0.9.0 | **0.9.x** |
| Serialize | 无 | **addon-serialize 0.14.x** |
| Clipboard | 无 | **addon-clipboard 0.2.x** |
| Attach | 无 | **addon-attach 0.12.x** |
| 布局 | 自研二叉树 | **保留树模型 + splitpanes 4.1.x** |
| 编辑器 | textarea | **CodeMirror 6** |
| 回放 | asciinema-player 3.17.0 | **3.17.x，按需加载** |
| API 客户端 | 手写 fetch | **openapi-typescript 7.13 + openapi-fetch 0.17** |
| 单测 | 无 | **Vitest 5.x + Vue Test Utils** |
| 浏览器测试 | Playwright 1.44 下限 | **Playwright 1.64.x + axe-core** |
| i18n | 硬编码中文 | **vue-i18n** |
| 代码质量 | `node --check` | **vue-tsc + ESLint + Prettier** |

### 4.3 依赖策略

- Python 用 `uv` 管理，提交 `uv.lock`；
- 前端用 `pnpm`，提交 `pnpm-lock.yaml`；
- 所有依赖使用兼容范围 + lockfile，不用裸 `latest`；
- CI 增加 `pip-audit`、`npm audit`、SBOM 生成；
- Renovate/Dependabot 每周更新，安全补丁优先；
- 发布产物附带 CycloneDX SBOM。

---

## 5. 网页终端增强

### 5.1 工作台布局

保留「一个标签页 = 一棵二叉树」的数据模型，但用 Vue 组件重写：

```text
Workspace
├─ TabBar
│   ├─ tab = { id, name, root, focus, dirty }
│   └─ pane-count badge
├─ SplitPane
│   ├─ leaf  = TerminalPane(sessionId)
│   └─ split = { dir, ratio, a, b }
├─ SessionSidebar
├─ CommandPalette
└─ StatusBar
```

功能：

- 标签拖拽排序、重命名、复制标签、关闭标签（detach）。
- pane 最大化/还原、交换、移动 pane 到其他标签、关闭 pane（不杀会话）。
- 分隔条拖拽 + 双击复位 + 键盘调整比例。
- 布局预设：单栏、左右、上下、2×2、三栏。
- 命令面板 `Ctrl/Cmd+K`：新建会话、分屏、切主题、搜索、分享、录制、打开文件。
- 快速切换 `Ctrl/Cmd+P`：按名称/ID 模糊搜索会话。
- 会话侧栏：运行中/已结束、按用户/后端/状态过滤。
- 布局持久化：localStorage 默认，可选服务端 `layouts` 表跨设备同步。
- 已结束会话保留 ghost pane，不挤掉兄弟布局。

### 5.2 WebSocket v2

#### 5.2.1 端点与协商

- 端点：`/ws/v2`
- 子协议：`wsctl.terminal.v2`
- 旧 `/ws`：返回 410/426，并说明最低客户端版本。
- 认证：沿用 Cookie / Bearer / share token。
- Origin、IP 白名单、连接帽、握手超时、控制帧限速全部保留。

#### 5.2.2 帧格式

```text
S→C binary:
  offset 0   kind   u8     1 = DATA
  offset 1   seq    u64    big-endian，payload 首字节的全局字节偏移
  offset 9   flags  u8     bit0 = snapshot, bit1 = replay, bit2 = last
  offset 10  payload bytes

C→S binary:
  offset 0   kind   u8     2 = INPUT
  offset 1   flags  u8
  offset 2   payload bytes

Text control:
  UTF-8 JSON，必须包含 {"v":2,"type":"..."}
```

#### 5.2.3 控制消息

| 方向 | type | 说明 |
|---|---|---|
| C→S | `attach` | `session`, `cols`, `rows`, `last_seq`, `generation`, `capabilities` |
| S→C | `attached` | `session`, `generation`, `seq`, `name`, `writable`, `snapshot_required` |
| C→S | `ack` | `seq`：表示 <= seq 的字节已写入 xterm |
| C→S | `resize` | `cols`, `rows` |
| C→S | `ping` | `nonce`，可带 `coalesced` |
| S→C | `pong` | `nonce` |
| S→C | `gap` | `from`, `to`, `reason`：服务端丢弃了未 ACK 的区间 |
| S→C | `snapshot` | `seq`, `format`, `data`：ANSI 屏幕快照 |
| S→C | `resync-begin` / `resynced` | 兼容旧语义，v2 中作为快照/增量的边界标记 |
| S→C | `exit` | `code` |
| S→C | `notice` / `error` / `evicted` | 运营提示，不写入终端缓冲 |
| S→C | `command` | Shell Integration 命令块事件 |
| S→C | `presence` | 在线观众/权限变化 |

#### 5.2.4 序号语义

- `seq` 是会话输出字节流的**全局字节偏移**，从 0 开始。
- 每个 DATA 帧的 `seq` 是 payload 首字节的偏移。
- 客户端 ACK 的 `seq` 表示「该偏移之前的所有字节都已交给 xterm」。
- 服务端维护 `ScrollbackRing` 的 `[base_seq, end_seq]`。
- attach 时：
  - `last_seq >= base_seq`：从 `last_seq` 开始补增量。
  - `last_seq < base_seq`：ring 已淘汰，`snapshot_required=true`。
  - `generation` 不一致：服务端已重启，必须快照/全量。
- 慢客户端：
  - 服务端按 ACK 计算 `unacked_bytes`。
  - 超过 `client_max_bytes` 时丢最旧的未 ACK 帧，发 `gap`。
  - 客户端收到 `gap` 后标记失步，请求 `snapshot`。
- 不允许「静默丢帧」：每一条 gap 都必须可解释。

#### 5.2.5 关闭码

保留 0.1.30 语义：

| 码 | 含义 | 是否重连 |
|---|---|---|
| 4400 | 协议错误 | 否 |
| 4401 | 未认证/登录失效 | 否 |
| 4403 | 无权/分享撤销 | 否 |
| 4404 | 会话不存在 | 否 |
| 4408 | 握手/空闲超时 | 是 |
| 4409 | 配额耗尽 | 否 |
| 4410 | 会话内存驱逐 | 是 |
| 4413 | 连接数上限 | 是 |
| 4429 | 持续输入超限 | 否 |
| 4500 | 服务端错误 | 否 |

v2 新增（可选）：

- 4426：协议版本不兼容（客户端应升级）；
- 4427：快照不可用（客户端应回退全量）。

### 5.3 恢复与快照

#### 5.3.1 三层恢复

1. **客户端缓存**
   - `@xterm/addon-serialize` 序列化当前屏幕；
   - IndexedDB 按 `session_id + generation` 存储 `{last_seq, serialized, updated_at}`；
   - 刷新/重连先恢复本地屏幕，再 attach 请求增量。
2. **服务端快照**
   - `SnapshotProvider` 接口：
     ```python
     class SnapshotProvider(Protocol):
         async def snapshot(self, session: TermSession) -> Snapshot | None: ...
     ```
   - `TmuxSnapshot`：`tmux capture-pane -e -p`，准确、低成本。
   - `PyteSnapshot`：维护影子屏幕，输出 ANSI 全屏重绘。
   - `RawSnapshot`：回退到 ring 回放 + `nudge_repaint`。
3. **兜底**
   - `nudge_repaint()` 保留，但只用于：
     - `snapshot_capable=false`；
     - 快照失败；
     - 客户端明确请求重绘。

#### 5.3.2 pyte 使用策略

| 模式 | 行为 | 适用 |
|---|---|---|
| `auto`（默认） | 检测到 alternate screen（`ESC[?1049h`）后维护影子屏幕 | TUI |
| `always` | 所有会话维护影子屏幕 | 高频重连、调试 |
| `off` | 不维护，按需从 ring 回放 | 极致低开销 |

- Sixel、Kitty graphics、未识别序列：标记 `snapshot_capable=false`。
- 影子屏幕每 N 字节做 checkpoint，避免从头回放。
- 快照生成在 worker 线程/协程中执行，不阻塞 PTY 读循环。

#### 5.3.3 失步状态机

```text
connected
  ├─ gap received → desynced
  ├─ snapshot requested → snapshotting
  ├─ snapshot applied → connected
  ├─ snapshot failed → desynced (fallback nudge)
  └─ attach with generation mismatch → resyncing
```

UI 状态：

- 状态栏：已连接 / 重连中 / 失步 / 快照中 / 已结束；
- pane 角标：失步；
- 失步条：说明「丢了多少字节 + 已自动恢复/需要手动重同步」；
- 不再使用笼统的「输出过快，部分内容已省略」作为唯一解释。

### 5.4 Shell Integration 与命令块

#### 5.4.1 协议

解析：

- OSC 133：`A` prompt start、`B` command start、`C` command executed、`D;exit` command finished。
- OSC 633：`E;command`、`P;cwd`、`C` 等 VS Code 兼容扩展。

#### 5.4.2 CommandBlock

```json
{
  "id": "cmd_01J...",
  "session": "a1b2c3",
  "command": "pytest -q",
  "cwd": "/srv/app",
  "started_at": 1730000000.123,
  "ended_at": 1730000012.456,
  "duration_ms": 12333,
  "exit_code": 1,
  "status": "failed"
}
```

能力：

- 终端左侧命令 gutter；
- 点击命令块选中输出；
- 复制输出；
- 重新运行（写入同一会话）；
- 跳转到上一个/下一个提示符；
- 失败命令红色标记；
- 底部状态条显示当前 cwd / 退出码 / 耗时；
- 可选审计 `command` 事件（不记录输出，除非 `audit_input`）。

#### 5.4.3 Shell 集成安装

```bash
wsctl shell-integration install          # 自动识别 $SHELL
wsctl shell-integration install bash
wsctl shell-integration show zsh
```

- 默认 opt-in；
- 不修改用户 shell 的默认配置，除非显式 `--write-rc`；
- 支持 bash / zsh / fish / PowerShell；
- 脚本只输出 OSC，不执行额外命令；
- 未安装时 UI 自动降级，不报错。

### 5.5 性能优化

| 层 | 措施 | 目标 |
|---|---|---|
| PTY | 预分配读缓冲、`os.readv`/`memoryview`、每 4–8ms 合批 | 降低 syscall/分配 |
| 会话 | SessionActor 串行化、无锁广播快照、每客户端有界队列 | 消除锁竞争 |
| 协议 | 二进制数据帧、ACK、增量恢复、快照 | 减少重传 |
| 服务端 | `uvloop` + `httptools`、orjson/msgspec | 提升吞吐 |
| 浏览器 | xterm 6 WebGL → Canvas → DOM、每动画帧一次 write、256 KiB 上限 | 60fps |
| 布局 | ResizeObserver、仅可见 pane fit、切换标签 0 resize | 消除空白帧 |
| 加载 | Vite 代码分割、终端核心优先、管理/编辑器/回放懒加载 | 首屏 < 500KB gzip |
| 文件 | 虚拟列表、分页、预览/编辑 Worker | 万级目录不卡 |

### 5.6 分享与协作 v2

- 一个会话支持多条分享链接：
  - `read`：只读；
  - `write`：可输入；
  - `control`：可 resize / 控制；
  - 标签、有效期、最后使用时间、创建者。
- 干净 URL：`/s/<token>`；
- 旧 `?session=&share=` 形式保留一个版本重定向；
- 在线观众列表；
- 写权限仲裁：
  - 默认最后 resize 生效；
  - `control` 权限可显式 `claim_control`；
  - 只读观众永不 resize。
- 撤销立即生效，保留 4403 语义；
- 分享访问写审计。

### 5.7 文件与编辑器

- 文件列表虚拟化，支持万级目录；
- CodeMirror 6：
  - 语法高亮；
  - 搜索/替换；
  - 撤销/重做；
  - 脏检查；
  - 保存快捷键；
- 图片/文本预览保留；
- 大文件上传保留进度/取消/409 覆盖确认；
- 可选分片续传（tus）作为 0.2.x 增强；
- 拖拽到具体目录、批量上传、上传队列。

### 5.8 通知、PWA、移动端

- Service Worker + Web Push（VAPID，默认关闭）：
  - 会话响铃；
  - 会话退出；
  - 长命令完成（可选）。
- PWA：
  - app shell 缓存；
  - 离线显示「需要网络」；
  - 安装提示。
- 移动端：
  - 触控工具栏（Ctrl / Esc / Tab / 方向键）；
  - 手势切换标签；
  - 字号手势；
  - 窄屏自动堆叠 pane。
- 通知内容不写入终端缓冲，沿用 0.1.30 契约。

### 5.9 主题、国际化、无障碍

- 主题：
  - 内置主题保留；
  - 支持导入 iTerm2 / Windows Terminal / GNOME Terminal 配色；
  - 自定义 JSON；
  - 主题预览。
- 国际化：
  - `vue-i18n`；
  - zh-CN 默认，en 第二语言；
  - 日期/数字/时长本地化。
- 无障碍：
  - 屏幕阅读器模式；
  - 高对比主题；
  - `prefers-reduced-motion`；
  - 键盘可达；
  - axe-core 0 critical。

---

## 6. 后端架构

### 6.1 分层

```text
server/api/          FastAPI routers, schemas, dependencies
server/services/     application services（事务边界）
server/domain/       SessionActor, Protocol, Scrollback, Share, CommandBlock
server/infra/        SQLAlchemy, PTY, tmux, snapshot, metrics, audit
server/security/     auth, origin, proxy, rate limit
server/observability/ logging, metrics, tracing
```

规则：

- Router 不直接碰 Store/PTY；
- Service 负责事务与权限；
- Domain 不依赖 FastAPI；
- Infra 可替换、可测试；
- 所有跨模块调用通过显式接口。

### 6.2 SessionActor

每个会话一个 actor：

```text
mailbox:
  Attach(client, writable, share)
  Detach(client)
  Input(bytes)
  Resize(cols, rows)
  Ack(client, seq)
  Snapshot(reason)
  Stop(reason, status)
  RecordStart(path, record_input)
  RecordStop()
```

优势：

- 状态变更串行，消除 `_lock`；
- 广播顺序确定；
- 背压/丢弃在 actor 内决策；
- 每个 client 独立 sink，不阻塞 PTY 读；
- 可单测：actor + fake PTY + fake client。

`SessionManager` 保留外部 API，只做 id → actor 路由。

### 6.3 终端运行时

- `ScrollbackRing`：
  - 存储 `(start_seq, bytes)`；
  - 按字节序号淘汰；
  - 保持 ANSI/UTF-8 边界对齐；
  - 提供 `since(last_seq)` 与 `snapshot_range()`。
- `ProtocolCodec`：
  - 编码/解码二进制帧；
  - 校验 seq、flags、长度；
  - 控制帧 msgspec 模型；
  - 版本协商。
- `SnapshotProvider`：
  - `TmuxSnapshot`；
  - `PyteSnapshot`；
  - `RawSnapshot`。
- `CommandBlockParser`：
  - OSC 133/633 流式解析；
  - 事件写入 actor；
  - UI 订阅。
- `Recorder`：
  - 保留 asciinema cast v2；
  - 可选 cast v3（事件 + 输入）；
  - 写入失败自关闭。

### 6.4 数据层

**技术：SQLAlchemy 2.1 + Alembic，SQLite（0.2.0 唯一支持；PostgreSQL 0.3）。**

Schema v6 草案：

```text
users
  id, username, password_hash, role, totp_secret, disabled,
  created_at, password_changed_at

auth_sessions
  id, user_id, token_hash, ip, user_agent, created_at, expires_at, last_seen

api_keys
  id, user_id, name, token_hash, scopes, created_at, expires_at, last_used

term_sessions
  id, name, owner_id, backend, command, argv_json, env_json, cwd,
  status, generation, last_seq, snapshot_mode, shell_integration,
  instance_id, created_at, last_active

shares
  id, term_session_id, token_hash, scope, label,
  created_by, created_at, expires_at, last_used

recordings
  id, term_session_id, owner_id, path, size,
  started_at, ended_at, status

audit_events
  id, user_id, term_session_id, event, payload_json, ip,
  request_id, ts, prev_hash, hash

instances
  id, pid, host, started_at, heartbeat

settings
  key, value_json

layouts
  id, user_id, name, layout_json, updated_at

command_blocks（可选）
  id, term_session_id, command, cwd, started_at, ended_at,
  duration_ms, exit_code, status

push_subscriptions（可选）
  id, user_id, endpoint, keys_json, created_at
```

迁移策略：

1. Alembic baseline 接住 v5 schema；
2. `0002_v6` 迁移新增表/列；
3. 旧 `term_sessions.share_*` 迁移到 `shares`；
4. 旧 `audit_logs` 迁移到 `audit_events`（hash 链从迁移点开始）；
5. 启动时自动迁移前先做一致性备份；
6. `wsctl migrate --check` 只读检查，`wsctl migrate --apply` 手动执行；
7. 升级前强制 `wsctl backup`。

### 6.5 配置 v2

```toml
[server]
host = "127.0.0.1"
port = 7681
reuse_port = true

[auth]
required = true
session_ttl = "12h"
session_sliding_ttl = false
cookie_secure = false

[security]
trust_proxy = false
trusted_proxies = []
allowed_origins = []
allowed_ips = []

[terminal]
default_shell = "/bin/bash"
default_cwd = "/home/me"
default_backend = "local"
scrollback_bytes = "8MiB"
session_memory_limit = "64MiB"
max_sessions = 64

[files]
root = "/home/me"
max_upload = "100MiB"

[recording]
auto_record = false
record_input = false

[observability]
metrics_enabled = true
log_level = "info"
log_json = false
webhook_url = ""

[data]
data_dir = "/var/lib/wsctl"
```

规则：

- 分节 TOML；
- `config set server.port 9000`；
- 人类可读的 `12h` / `8MiB`；
- 每个字段声明 `apply = "immediate" | "new_object" | "restart"`；
- Web 管理台按分节分组；
- 只允许 `immediate/new_object` 在线写入；
- `wsctl config migrate` 自动转换 v1，保留注释、行尾、备份；
- 环境变量兼容现有扁平名，新增 `WSCTL_SERVER__PORT`。

### 6.6 API v2

| 资源 | 端点 |
|---|---|
| 认证 | `POST /api/v2/auth/login`、`POST /api/v2/auth/logout`、`GET /api/v2/auth/me` |
| 会话 | `GET/POST /api/v2/sessions`、`GET/PATCH/DELETE /api/v2/sessions/{id}` |
| 历史 | `GET /api/v2/sessions/history`、`GET /api/v2/sessions/{id}/detail` |
| 分享 | `GET/POST /api/v2/sessions/{id}/shares`、`DELETE /api/v2/shares/{id}` |
| 录制 | `POST /api/v2/sessions/{id}/recording/start|stop`、`GET /api/v2/recordings` |
| 文件 | `GET /api/v2/files`、`GET /api/v2/files/download|preview|image`、`PUT /api/v2/files/content` |
| 用户 | `GET/POST/PATCH/DELETE /api/v2/users` |
| 审计 | `GET /api/v2/audit`、`GET /api/v2/audit/verify` |
| 配置 | `GET/PUT /api/v2/config`、`POST /api/v2/config/reload` |
| 事件 | `GET /api/v2/events`（SSE） |
| 概览 | `GET /api/v2/overview` |
| 健康 | `GET /healthz`、`GET /readyz`、`GET /metrics` |

约定：

- OpenAPI 3.1；
- 错误 RFC 9457 Problem Details；
- 游标分页；
- `Idempotency-Key` 支持创建/上传；
- `ETag` / `If-None-Match` 用于配置与静态资源；
- SSE 只承载状态/审计事件，终端数据只走 WebSocket；
- 前端用 `openapi-typescript` + `openapi-fetch` 生成客户端，不手写路径。

### 6.7 安全

保留 0.1.30 全部保护，并新增：

- `limits` 替换自研限速；
- API Keys：哈希存储、scope、过期；
- 审计哈希链：`prev_hash` + `hash`，`/audit/verify`；
- CSP 去掉 script `unsafe-inline`，使用 hash/nonce；
- vendor 资源 SRI；
- SBOM（CycloneDX）、`pip-audit`、`npm audit`；
- Sigstore 签名、SLSA provenance；
- 可选 WebAuthn/Passkeys；
- 可选 OIDC（0.3）。

明确边界：

- wsctl 仍是 RCE 服务，按 SSH 对待；
- RBAC 控制会话可见性，不是命令级沙箱；
- 文件面板不扩大权限面；
- 分享链接是 bearer token，必须可撤销/可过期。

### 6.8 可观测性

指标：

- 保留现有 `wsctl_*` 指标名；
- 新增：
  - `wsctl_ws_ack_lag_bytes`
  - `wsctl_ws_snapshot_total{result}`
  - `wsctl_terminal_input_latency_seconds`
  - `wsctl_session_actor_mailbox_depth`
  - `wsctl_ws_gap_total{reason}`

追踪：

- OpenTelemetry span：HTTP、WS attach、replay、snapshot、PTY read、audit flush；
- `request_id` / `session_id` / `user_id` 脱敏后注入日志；
- 采样率可配。

日志：

- structlog JSON；
- 字段：ts、level、logger、event、request_id、user_id、session_id、duration_ms、error；
- 敏感字段脱敏。

健康：

- `/healthz`：进程存活 + 版本 + pid + instance_id；
- `/readyz`：DB、磁盘、tmux 可选依赖、维护循环心跳。

---

## 7. 破坏性变更与迁移

| 领域 | 变更 | 迁移 |
|---|---|---|
| Python | 最低 3.12 | 升级运行环境 |
| WebSocket | `/ws/v2`，旧 `/ws` 410/426 | 浏览器与 CLI 同步升级 |
| REST | `/api/v2`，旧 `/api` 移除 | 客户端/脚本升级 |
| 配置 | v2 分节 TOML | `wsctl config migrate`，自动备份 |
| 数据库 | schema v6，Alembic | 启动自动迁移；升级前 `wsctl backup` |
| 前端 | 需要 Node/pnpm 构建 | wheel 内置预构建产物，用户无感 |
| localStorage | layout/prefs v2 | 首次启动迁移；失败则重置并提示 |
| 分享 | 多链接 + `/s/<token>` | 旧 token 迁移为默认分享；旧 query 重定向一个版本 |
| 测试 | 删除 59 条 JS 源码字符串契约 | 替换为 Vitest + Playwright 行为契约 |
| 后端 | SQLAlchemy/Alembic | 先 baseline 再迁移 repository |

迁移命令：

```bash
wsctl backup ~/wsctl-before-0.2.0.tar.gz --include-config
wsctl migrate --check
wsctl migrate --apply
wsctl doctor
```

回退：

- 0.2.0 升级前自动备份数据库与配置；
- 回退必须 `wsctl restore`，不支持旧版本直接读新库；
- 不支持与 0.1.x 客户端/服务端混跑。

---

## 8. 里程碑与排期

按 1–2 名全职开发估算：

| 阶段 | 周期 | 交付 | 出口条件 |
|---|---|---|---|
| **M0 基线** | 1 周 | ADR、性能基准、发布清理、0.1.30 冻结 | 基准数据入库；`dist/` 清理；CI 可复现 |
| **M1 工程底座** | 2 周 | Python 3.12/3.13、uv.lock、SQLAlchemy/Alembic baseline、配置 v2、structlog/prometheus/OTel、service 层 | 旧测试全绿；v5 库自动迁移测试通过 |
| **M2 协议 v2** | 3 周 | SessionActor、ScrollbackRing(seq)、ACK/resume/gap/snapshot、Python/TS 参考客户端 | 协议 fuzz + 断线/丢帧/重连场景全绿 |
| **M3 前端骨架与 parity** | 3 周 | Vue/TS/Vite、xterm 6、登录/终端/标签/分屏/文件/分享/设置/管理基础 | 0.1.30 功能矩阵逐项通过 |
| **M4 工作台增强** | 2 周 | 命令面板、Shell Integration/命令块、CodeMirror、移动端、i18n、PWA | 新功能 e2e + 视觉回归通过 |
| **M5 管理面与安全** | 2 周 | API v2 完整、SSE、审计哈希链、API Keys、分享 v2、可观测性 | OpenAPI 契约测试、安全审查通过 |
| **M6 硬化** | 2 周 | 性能/混沌/跨浏览器/a11y、迁移演练、文档、Docker 多阶段 | 所有验收门槛达标 |
| **M7 RC/发布** | 1 周 | TestPyPI、真实数据迁移演练、RC、正式发布 | 迁移回滚演练成功；发布检查单全绿 |

总计约 **15–16 周**；单人开发建议按 20 周估算。

### 8.1 并行工作流

```text
Track A 后端/协议   M1 ── M2 ────────── M5 ── M6
Track B 前端/工作台       M1 ── M3 ── M4 ── M6
Track C 数据/迁移   M1 ────────── M2 ── M5 ── M6
Track D 测试/发布   M0 ────────────────── M6 ── M7
```

- M1 三条线共享骨架；
- M2 协议先于前端；
- M3 前端先用 mock 协议，再切真实 v2；
- M6 合并三条线做全量回归。

---

## 9. 验收标准

### 9.1 功能 parity 矩阵

| 能力 | 0.1.30 | 0.2.0 | 验收 |
|---|---|---|---|
| 多会话/多标签 | ✅ | ✅ 改进 | 标签拖拽/命令面板 |
| 分屏 | ✅ | ✅ 改进 | 最大化/交换/预设/键盘比例 |
| 断线重连 | ✅ 字节回放 | ✅ 增量+快照 | TUI 无花屏、无白屏 |
| 只读/可写分享 | ✅ 单链接 | ✅ 多链接/scope | 旧 token 迁移 |
| 文件面板 | ✅ | ✅ 改进 | CodeMirror/虚拟列表 |
| 录制回放 | ✅ | ✅ | cast v2 兼容 |
| 审计 | ✅ | ✅ 改进 | 哈希链/SSE |
| 用户/RBAC/TOTP | ✅ | ✅ | API v2 |
| 配置热更新 | ✅ | ✅ 改进 | 分节/模型驱动 |
| tmux/SSH | ✅ | ✅ | 后端保留 |
| CLI | ✅ | ✅ 改进 | stdlib HTTP + 协议 v2（`httpx/tenacity` 未作为运行时依赖） |
| 观测 | ✅ | ✅ 改进 | OTel/标准指标 |

### 9.2 性能门槛

| 指标 | 目标 |
|---|---|
| T1 `seq 1 200000` | ≤ 6s（当前门槛 8s） |
| 10 pane 并发洪水 | ≤ 20s，零 viewer 丢失、零驱逐 |
| 事件循环 p99 延迟 | < 20ms（当前门槛 50ms） |
| 输入到回显 p95 | < 30ms（LAN） |
| 重连恢复完整屏幕 p95 | < 300ms |
| 首屏 JS | < 500KB gzip；终端核心优先加载 |
| 100 空闲会话 RSS | < 250MB；单空闲会话 < 1.5MB |
| WS 控制开销 | < 5%；数据帧不经过 JSON |

### 9.3 质量门槛

- `ruff` + `mypy strict` + `pytest` 全绿；
- Vitest 覆盖 protocol/store/composable；
- Playwright 覆盖 Chromium/Firefox/WebKit；
- axe-core 0 critical；
- 关键页面视觉回归通过；
- 混沌：断网、延迟、丢包、服务重启、慢消费者、磁盘满；
- 迁移：真实 v5 数据库 + v1 配置 + v1 localStorage → 0.2.0；
- 安全：`pip-audit` / `npm audit` 0 high/critical；CSP 无 script `unsafe-inline`。

---

## 10. 测试策略

### 10.1 测试金字塔

```text
        Playwright E2E（跨浏览器、视觉、a11y）
      ─────────────────────────────────────
      Vitest 组件/Store/Protocol 单测
    ───────────────────────────────────────
    pytest 后端单元/集成/协议/迁移/性能
  ─────────────────────────────────────────
  Hypothesis fuzz + Schemathesis API 契约
```

### 10.2 后端

- 保留现有 pytest 行为测试；
- 新增 `tests/protocol/`：
  - 帧编解码；
  - seq/ack/gap；
  - attach/resume/snapshot；
  - 非法帧 fuzz；
  - 版本协商；
- 新增 `tests/migration/`：
  - v5 DB → v6；
  - v1 config → v2；
  - 分享迁移；
  - 失败回滚；
- 新增 `tests/perf/`：
  - T1/T2/T3/T6；
  - 首屏/重连/内存；
  - 结果写入 JSON，CI 对比基线；
- 新增 `tests/chaos/`：
  - TCP 代理延迟/丢包；
  - 服务端 kill/restart；
  - 慢消费者；
  - 磁盘满。

### 10.3 前端

- Vitest：
  - `ProtocolClient`；
  - `useTerminal`；
  - `useLayout`；
  - `useConnection`；
  - `CommandBlockParser`；
  - `prefs` 迁移；
- Vue Test Utils：
  - TabBar；
  - SplitPane；
  - CommandPalette；
  - Settings；
  - FilePanel；
- Playwright：
  - 拆分 0.1.30 的 `test_browser_flow` 巨型用例；
  - 登录、终端、分屏、文件、分享、录制、管理、设置；
  - 跨浏览器；
  - 视觉回归；
  - axe；
  - 网络故障注入。

### 10.4 删除的测试

删除 `tests/test_ui_contracts.py` 中 59 条解析 `app.js` 源码字符串的契约，替换为：

- Vitest 行为测试；
- Playwright 行为测试；
- 少量跨文件契约（如 OpenAPI 类型生成结果与后端一致）。

不要试图保留这些字符串契约；它们会让前端重构寸步难行。

---

## 11. 发布策略

### 11.1 版本节奏

```text
0.2.0a1 → 0.2.0a2 → 0.2.0b1 → 0.2.0rc1 → 0.2.0
```

- alpha：协议 v2 + 后端骨架，前端可用但功能不全；
- beta：工作台 parity，开始迁移演练；
- rc：冻结功能，只修 bug；
- 正式：TestPyPI 验证后发布 PyPI。

### 11.2 发布产物

- wheel：包含预构建前端资产；
- sdist：包含 Python 源码、`web/` 源码、预构建资产、锁文件；
- Docker：多阶段构建（Node 构建前端 + Python 运行时）；
- SBOM：CycloneDX；
- 签名：Sigstore；
- GitHub Release：自动生成 + 迁移说明。

### 11.3 发布检查单

- [ ] `uv.lock` / `pnpm-lock.yaml` 已更新；
- [ ] `ruff` / `mypy` / `pytest` / `vitest` / `playwright` 全绿；
- [ ] 性能门槛达标；
- [ ] 迁移演练通过；
- [ ] 安全扫描通过；
- [ ] 文档更新（README / DESIGN / CHANGELOG / MIGRATION）；
- [ ] `dist/` 清理并重建；
- [ ] Docker 镜像冒烟；
- [ ] TestPyPI 安装验证；
- [ ] 回滚演练。

---

## 12. 风险与取舍

| 风险 | 说明 | 缓解 |
|---|---|---|
| 一次性换前端 + 协议 + 存储 | 范围大，回归面广 | 分 M1–M3 三条并行分支；协议先于 UI；alpha/beta 分阶段 |
| pyte 快照不完整 | Sixel/罕见序列可能不支持 | `snapshot_capable=false` 回退；tmux 用 capture-pane；保留 nudge |
| SQLAlchemy 迁移风险 | 旧测试与 SQL 细节耦合 | Alembic baseline 先接管 schema；repository 逐模块迁移；升级前备份 |
| 前端重写回归 | 59 条结构契约失效 | 不保留字符串契约；用 Vitest/Playwright 重建行为矩阵 |
| 包体积/首屏变慢 | Vue + UI 库 + xterm | 代码分割；管理/编辑器/回放懒加载；预算门禁 |
| 分享链接断裂 | 多链接 + 新 URL | 旧 token 迁移；旧 query 形式重定向一个版本 |
| Node 供应链 | 新增 pnpm 依赖 | lockfile、Renovate、npm audit、SBOM、固定 Node 24 |
| FastAPI/Starlette 升级 | 行为差异 | 分支先跑全量测试；锁定版本；保留回退 tag |
| 性能门槛过严 | CI 抖动 | 记录基线；相对阈值 + 绝对上限；独立 slow job |

---

## 13. 待决策项

请爸爸拍板：

1. **前端框架**：推荐 Vue 3；是否接受？
2. **UI 组件**：推荐 Naive UI + Tailwind；是否接受，还是用 Radix Vue + Tailwind？
3. **布局引擎**：推荐保留二叉树 + splitpanes；是否要更激进的 dockview 浮动面板？
4. **数据库**：0.2.0 是否同时支持 PostgreSQL，还是先 SQLite、0.3 再加？
5. **pyte 快照**：默认 `auto`（仅 TUI 维护影子屏幕）是否接受？
6. **Web Push**：0.2.0 是否做，还是留到 0.3？
7. **API v2**：是否保留 `/api` 一个版本的 410 提示，还是直接移除？
8. **分享 URL**：是否保留旧 query 形式重定向，还是彻底切 `/s/<token>`？
9. **Python 基线**：3.12 还是 3.13？推荐 3.12 + 主测 3.13。
10. **发布周期**：15–16 周是否可接受，还是压缩到 10–12 周先出 beta？

---

## 14. 第一批 PR

按顺序：

1. **`docs: ADR-0001 前端技术栈`**
   - Vue/Vite/TS/Naive UI/splitpanes 选型；
   - 不选 React/dockview 的理由；
   - 构建与发布流程。

2. **`chore: 0.1.30 baseline benchmark`**
   - T1/T2/T3/T6；
   - 首屏、重连、内存、包体积；
   - 结果写入 `docs/benchmarks/0.1.30.json`。

3. **`feat(protocol): v2 codec + seq/ack 单测`**
   - 不碰 UI；
   - 帧格式、ACK、gap、snapshot；
   - Hypothesis fuzz。

4. **`feat(session): SessionActor + ScrollbackRing(seq)`**
   - 保留 `SessionManager` 外部 API；
   - 旧 session 测试继续跑；
   - 新增 actor 单测。

5. **`feat(db): Alembic baseline + v6 迁移`**
   - 先接住 v5；
   - 再迁移 v6；
   - 真实 v5 库迁移测试。

6. **`feat(web): Vite/Vue 脚手架 + xterm 6 smoke`**
   - 登录、单终端、重连、分屏；
   - 暂不追求 parity。

7. **`ci: frontend build + Vitest + Playwright + bundle budget`**
   - 0.2.0 质量门从第一天生效。

8. **`feat(config): v2 分节 + 迁移命令`**
   - 保留注释/行尾；
   - 自动备份；
   - Web 编辑器适配。

9. **`feat(protocol): snapshot provider（tmux/pyte/raw）`**
   - TUI 恢复；
   - 失败回退。

10. **`feat(web): workspace layout + command palette`**
    - 标签/分屏/预设/快速切换。

11. **`feat(terminal): shell integration + command blocks`**
    - OSC 133/633；
    - 命令 gutter；
    - 复制/重跑。

12. **`feat(api): v2 routers + OpenAPI + generated client`**
    - 前端切换生成客户端；
    - 删除手写路径。

---

## 15. 附录

### A. WebSocket v2 示例

```json
// C→S attach
{"v":2,"type":"attach","session":"a1b2c3d4","cols":120,"rows":30,
 "last_seq":123456,"generation":"8f3c...","capabilities":["snapshot","osc133"]}

// S→C attached
{"v":2,"type":"attached","session":"a1b2c3d4","generation":"8f3c...",
 "seq":123456,"name":"bash","writable":true,"snapshot_required":false}

// S→C gap
{"v":2,"type":"gap","from":123000,"to":123456,"reason":"shed"}

// C→S ack
{"v":2,"type":"ack","seq":123456}

// S→C snapshot
{"v":2,"type":"snapshot","seq":123456,"format":"ansi","data":"\u001b[2J..."}
```

### B. 配置 v2 示例

见 6.5。

### C. 数据库草案

见 6.4。

### D. 目录结构

```text
wsctl/
├── pyproject.toml
├── uv.lock
├── package.json
├── pnpm-lock.yaml
├── web/                         # Vue 源码
│   ├── src/
│   │   ├── api/                 # generated client
│   │   ├── components/
│   │   ├── composables/
│   │   ├── protocol/
│   │   ├── stores/
│   │   ├── styles/
│   │   └── main.ts
│   ├── index.html
│   └── vite.config.ts
├── src/wsctl/
│   ├── server/
│   │   ├── api/
│   │   ├── services/
│   │   ├── domain/
│   │   ├── infra/
│   │   ├── security/
│   │   └── observability/
│   ├── core/
│   ├── cli/
│   └── static/                  # 构建产物（wheel 内置）
├── tests/
│   ├── protocol/
│   ├── migration/
│   ├── perf/
│   ├── chaos/
│   └── ...
└── docs/
    ├── ROADMAP-0.2.0.md
    ├── ADR/
    ├── MIGRATION.md
    └── benchmarks/
```

### E. 性能基准记录模板

```json
{
  "version": "0.2.0",
  "date": "2026-11-04",
  "commit": "...",
  "env": {
    "cpu": "...",
    "memory": "...",
    "python": "3.13.x",
    "node": "24.x"
  },
  "t1_seq_200000_ms": null,
  "t2_8_sessions_ms": null,
  "t6_10_panes_ms": null,
  "event_loop_p99_ms": null,
  "input_echo_p95_ms": null,
  "reconnect_p95_ms": null,
  "first_load_gzip_kb": null,
  "rss_100_idle_sessions_mb": null
}
```

---

## 16. 结语

0.2.0 的关键不是「加更多按钮」，而是把终端从「一个能连上的 xterm」变成「一个可以长期工作的终端工作台」。

优先级：

1. **协议 v2 + 快照恢复**：从能用变可靠；
2. **Vue/TS/Vite 工作台**：从终端框变工作台；
3. **SQLAlchemy/Alembic + 配置 v2 + 可观测性**：从个人项目变可长期维护产品。

只要这三件事做成，0.2.0 就会是 wsctl 真正意义上的「现代 Python Web Terminal」。
