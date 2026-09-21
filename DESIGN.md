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
- **进程治理**：`start_new_session` + `killpg`，并在会话结束时 `wait()` 回收；未安装
  全局 SIGCHLD handler（避免与 `subprocess` 争抢 PID）。已跟踪会话不残留僵尸进程。

### 3.1 可选 tmux 后端（跨重启恢复）

`backend="local"`（默认）时 shell 是服务进程的直接子进程，服务退出即随之结束。
`backend="tmux"` 时，PTY 里跑的是 `tmux new-session -A -s wsctl-<sid> <cmd>`，即一个
tmux 客户端；真正的 shell 活在 tmux server 内。于是：

- 服务关闭时以 `preserve=True` 停止会话——只断开 tmux 客户端，shell 继续运行；
- 下次启动读取 `term_sessions` 中 `backend='tmux'` 的行，用相同 id 重新 attach，
  tmux 负责重绘屏幕；
- 管理员 kill / 空闲回收 / 寿命到期则 `preserve=False`，真正 `tmux kill-session`。

tmux 为可选依赖（`wsctl[persist]`），默认路径不依赖它。

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

## 6. CLI 命令树（已实现）

```
wsctl serve                     # 起控制面，默认配置开箱即用
wsctl serve --new "bash"        # 启动时顺带开一个会话
wsctl serve --backend tmux      # 默认使用 tmux 后端（跨重启恢复）
wsctl session list|new|kill|attach   # new 支持 --backend local|tmux
wsctl connect [url] [-s id]     # 瘦客户端：本地 raw 终端直连
wsctl login|logout              # 缓存/清除服务端凭据
wsctl user add|list|del|passwd|role|totp
wsctl audit                     # 查看审计日志（admin）
wsctl config show|path|edit
wsctl version
```

> `config set` 未实现：配置以文件为准，用 `config edit` 修改（避免命令行与文件两套写入口）。


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

> tmux 后端需要系统安装 `tmux`（非 pip 依赖），未安装时自动回退 local。

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
| **M6** | 缺口补齐：`session new/attach`、`serve --new`、`config edit`、会话重命名、每会话连接上限、输入速率限制、并发压测 |
| **M7** | 可选 tmux 后端：会话跨服务重启恢复；优雅关闭时保留 tmux 会话；启动自动 reattach |
| **M8** | 会话只读分享：share token（可限时/可撤销）+ 二维码 + 匿名只读观看（输入被拒） |
| **M9** | SSH 后端：`backend=ssh` 结构化目标（host/user/port/identity/options/远端命令） |
| **M10** | 录制回放：asciinema cast v2 录制（可含输入）+ 自动录制 + 下载 API/CLI |
| **M11** | 优雅重启：SO_REUSEPORT 监听 socket，新实例先接管再停旧实例（零停机） |

## 10.1 实现状态（截至 0.1.0）

**已实现**：M0–M11 全部交付项；二进制 WS 协议、会话与连接解耦、重连回放、
多用户 RBAC、审计 + `/api/audit`、登录限速、IP allowlist、TOTP、安全响应头、
文件面板（防穿越 + 上传限流）、`/metrics`、JSON 日志、`connect` 瘦客户端、
会话重命名、每会话连接上限、输入令牌桶限速、并发/大输出压测、**可选 tmux 后端
（会话跨服务重启恢复）**、**只读分享（share token + 二维码 + 匿名观看）**、
**SSH 后端**、**asciinema 录制回放**、**SO_REUSEPORT 零停机重启**。

**尚未实现（见 §11 Backlog）**：可写分享、`settings` 表与 `term_sessions`
完整字段、主题/字体/快捷键可配、移动端专项适配、`config set`、录制的前端回放 UI。

> 说明：进程回收依赖 `start_new_session` + `killpg` 与 `TermSession` 结束时的
> `wait()`，未安装全局 SIGCHLD handler（避免与 `subprocess` 争抢 PID）；已跟踪
> 会话不会残留僵尸进程。

## 11. Backlog（后续）

ZMODEM/lrzsz · Sixel · Webhook · 可写分享 · 录制前端回放 UI ·
主题市场 · `settings` 表与 `term_sessions` 完整字段 ·
主题/字体/快捷键可配 · 移动端专项适配 · `config set`

## 12. 发布

- 包名 / PyPI：`wsctl`（已验证可用）
- GitHub：`ThzxxArt/wsctl`
- 版本：语义化；初始 `0.1.0`
- CI：ruff + mypy + pytest（py3.11 / 3.12 / 3.13）
- 发布：tag 触发 PyPI Trusted Publishing（OIDC，无需存 token）
