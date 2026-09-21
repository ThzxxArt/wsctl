# wsctl 设计文档

> 版本：0.1.0 · 状态：草稿（v1 开发中）
> 作者：ThzxxArt · 许可：MIT

## 1. 定位

**wsctl 是 ttyd 的现代 Python 超集。**

单机部署的 Web 在线终端。一条命令起服务，浏览器或 CLI 得到完整 shell；终端
**会话独立于连接**，断线重连即恢复。

- 目标场景：个人 / 小团队的远程运维、跳板、演示、受限环境终端访问
- 部署形态：单进程单机，`pip install wsctl` 零编译即用
- 对照参照：`ttyd`（C + libuv/libwebsockets）

### 1.1 明确边界（诚实声明）

wsctl **不在原始吞吐上对标 ttyd**。纯 Python 无法在 `cat` 大文件、`yes` 刷屏
这类高吞吐场景打赢 C+libuv。wsctl 的「稳定」来自**架构**——会话与连接解耦、
输出背压、进程隔离——而非单连接速度。请勿用不公平 benchmark 比较两者。

## 2. 相对 ttyd 的差异化

| 维度 | ttyd | wsctl |
|---|---|---|
| 会话模型 | 一进程 = 一会话 = 一端口 | 单服务托管 N 个会话 |
| 多终端 | 起多进程 / 依赖 tmux | 原生多会话 + 多标签 UI |
| 用户体系 | 单一 basic-auth 凭据 | 多用户 + RBAC（admin/user） |
| 审计 | 无 | 结构化审计日志（可扩展录制） |
| 连接语义 | 连接与会话耦合 | 解耦，断线重连回放屏幕 |
| 配置 | 命令行参数 | 配置文件 + CLI + 热更新 |
| 可观测 | 基础日志 | `/healthz` · `/metrics` · JSON 日志 |
| 管理面 | 无 | REST API（可扩展 Webhook） |
| 扩展 | C | Python |
| 安装 | 各平台二进制 | `pip install` 零编译 |

**主动选择放弃的项**：不兼容 ttyd 的命令行参数，另起炉灶使用子命令式 CLI。

## 3. 核心架构：会话与连接解耦

```
                ┌────────────── SessionManager (asyncio) ──────────────┐
 Browser WS ─┐  │  TermSession#a1                                       │
 Browser WS ─┼─►│   ├ attached[] ──► broadcast(out)                     │
 CLI     WS ─┘  │   ├ PtyMaster(fd) ──┬──┐                              │
                │   └ meta            │  │                              │
                │                ┌────▼──┴───────┐                      │
                │                │ Scrollback    │ 环形缓冲(原始ANSI流) │
                │                └───────────────┘                      │
                │           fork/exec → [bash | ssh | 命令]  ⟂连接独立 │
                │  TermSession#b2 ...                    ⟂生命周期独立 │
                └───────────────────────────────────────────────────────┘
```

- **attach / detach**：WebSocket 连接进入 / 离开 `attached[]` 集合，PTY 子进程
  生命周期不受影响。
- **输出路径**：PTY master → 写入 scrollback → 广播给所有 attached 客户端。
- **重连回放**：attach 时先发送 `resize`，再 replay scrollback 原始字节流；
  xterm.js 会依据 ANSI 转义序列自动重建屏幕状态。
- **背压**：每个客户端维护有界发送队列；慢消费者达到上限时丢弃旧帧或断开，
  防止大输出导致服务端内存暴涨。
- **回收策略**：空闲超时 / 最大生命周期 / 管理员显式 kill，三者之一触发回收。
- **进程治理**：统一处理 `SIGCHLD`，杜绝僵尸 / 孤儿进程。

## 4. WebSocket 协议

三端（浏览器 / CLI 瘦客户端）共用同一协议：**二进制帧承载原始终端字节**，
**文本帧承载 JSON 控制消息**。二进制可避免多字节 UTF-8 字符在分片边界被截断。

```
文本帧（JSON 控制）
C→S {"type":"attach","session":"a1","cols":120,"rows":30}
C→S {"type":"input","data":"..."}          # 备用：文本输入通道
C→S {"type":"resize","cols":120,"rows":30}
C→S {"type":"ping"}

S→C {"type":"attached","session":"a1","name":"..."}
S→C {"type":"exit","code":0}
S→C {"type":"error","msg":"..."}
S→C {"type":"pong"}

二进制帧（原始终端字节）
C→S <按键 / 粘贴内容>
S→C <输出，含重连时的 scrollback 回放>
```

**顺序保证**：`attach` 时在会话锁内先入队回放、再注册客户端，广播同样持锁，
从而保证「回放 → 实时输出」的顺序，且回放不会漏掉锁切换窗口内的输出。

**认证**

- 浏览器：登录后签发 HttpOnly / Secure / SameSite Cookie，握手时校验
- CLI：`Authorization: Bearer <token>`
- 两种方式均校验 **Origin 白名单**（防 CSWSH）

## 5. 数据模型（SQLite）

```sql
users(id, username, password_hash, role[admin|user], totp_secret, disabled, created_at)

auth_sessions(id, user_id, token_hash, ip, user_agent, exp, last_seen)   -- 登录态

term_sessions(id, name, owner_id, backend[local|ssh], command, argv, cwd, env,
              created_at, last_active, idle_timeout, max_life, status)    -- 终端会话元数据

audit_logs(id, user_id, term_session_id, event, payload, ip, ts)

settings(key, value)
```

> 命名约定：`auth_sessions` 指**登录态**，`term_sessions` 指**终端会话**。

## 6. CLI 命令树

```
wsctl serve                     # 起控制面，默认配置开箱即用
wsctl serve --new "bash"        # 顺带开一个会话（快速单会话模式）
wsctl session list|new|kill|attach
wsctl connect <url> [session]   # 瘦客户端：本地 raw 终端直连
wsctl user add|list|del|passwd|role
wsctl config show|set|edit
wsctl version
```

## 7. 目录结构

```
wsctl/
├── pyproject.toml                # hatchling · PyPI: wsctl · MIT
├── DESIGN.md  README.md  LICENSE  CHANGELOG.md
├── src/wsctl/
│   ├── __init__.py               # __version__
│   ├── __main__.py
│   ├── cli/                      # serve/session/connect/user/config
│   ├── server/                   # FastAPI: routes, ws, auth, security, deps
│   ├── core/
│   │   ├── session.py            # TermSession + SessionManager  ★核心
│   │   ├── pty.py                # PTY 抽象（posix / win extra）
│   │   ├── scrollback.py         # 环形缓冲
│   │   ├── store.py              # SQLite
│   │   └── config.py
│   └── static/                   # xterm.js + 前端（内嵌 wheel）
└── tests/
```

## 8. 依赖

```
fastapi  uvicorn[standard]  jinja2  typer  rich
pydantic-settings  argon2-cffi  python-multipart  itsdangerous  pyotp

[win] → pywinpty
[dev] → ruff mypy pytest pytest-asyncio httpx build twine
```

## 9. 安全清单（内建，非可选）

1. 默认强制认证，`argon2` 哈希
2. 会话 token 存 HttpOnly / Secure / SameSite Cookie；CLI 用 Bearer
3. WebSocket 握手 Origin 白名单校验（防 CSWSH）
4. 登录失败限速、IP allowlist
5. 可选 TOTP 二次验证
6. 全量审计日志
7. 会话数 / 空闲超时 / 速率上限
8. HTTPS/WSS：文档首选反向代理，内置 `--ssl` 兜底

## 10. 里程碑

| 阶段 | 交付 |
|---|---|
| **M0** | 脚手架 + pyproject + ruff/mypy + CI + MIT |
| **M1** | SessionManager + PTY 保活 + WS attach/detach + 重连回放 + 最小前端，跑通单会话 |
| **M2** | 多会话 + 多标签 UI + 多用户/RBAC |
| **M3** | 审计日志 + 安全加固（限速 / allowlist / TOTP / Origin）|
| **M4** | Web 文件面板 + 可观测（/healthz · /metrics · JSON 日志）|
| **M5** | CLI `connect` 瘦客户端 + 文档 + PyPI Trusted Publishing |

> 实现进度：M0 完成；M1 核心已落地（SessionManager、PTY 保活、attach/detach、
> 重连回放、二进制 WS 协议、最小前端、登录 + 会话创建）。M2 已落地（多会话归属 +
> RBAC、多标签 UI、空闲/寿命回收、管理员用户 API、CLI `login`/`session`）。
> M3 已落地（全量审计 + `/api/audit`、登录限速、IP allowlist、TOTP、安全响应头）。
> `connect` 瘦客户端待 M5。

## 11. Backlog（后续）

ZMODEM/lrzsz · Sixel · SSH 跳板 · asciinema 录制回放 · Webhook · 分享链接/二维码 · 主题市场

## 12. 发布

- 包名 / PyPI：`wsctl`（已验证可用）
- GitHub：`ThzxxArt/wsctl`
- 版本：语义化；初始 `0.1.0`
- CI：ruff + mypy + pytest（py3.11 / 3.12 / 3.13）
- 发布：tag 触发 PyPI Trusted Publishing（OIDC，无需存 token）
