# wsctl 设计文档

> 版本：0.1.2 · 状态：0.1.0/0.1.1 已发布；0.1.2 待发布
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

tmux 为可选的系统依赖（需在主机安装，非 pip 依赖），默认路径不依赖它。

### 3.2 多实例与实例租约

`--reuse-port` 让新旧实例短暂共享同一 `data_dir`。为了让两个实例互不干扰：

- 每个进程启动时生成一个 `instance_id`，写入 `instances` 表并每 5 秒续约；
  `instance_ttl`（默认 30 秒）内没有续约的实例被视为已死。
- `term_sessions.instance_id` 记录会话归属。维护循环调用
  `term_session_stop_missing(alive, instance_id=自己)`，**只**把本实例内存中已不存在的
  运行中会话标记为 stopped，绝不触碰仍存活的另一实例的会话。
- 启动时只接管「归属实例已死」的会话：tmux 会话重新 attach，local 会话标记 stopped；
  属于存活对端的会话原样跳过。
- 对账（`_reconcile_instances`）在启动**以及每个维护周期**运行：先按本机 PID 存活
  情况立即清除崩溃实例的租约（无需等 `instance_ttl`），再收养无主的 tmux 会话、回收
  无主的 local 会话。因此对端一退出，会话立刻被接管，而非等到下次重启。
- tmux 会话名带 `data_dir` 派生的命名空间（`wsctl-<ns8>-<sid>`），因此同机、不同
  data_dir 的两个部署即使共用同一个 tmux server 也不会互相看到或误杀。
- 启动引导（创建 admin）容忍并发唯一约束冲突，避免多实例同时首启崩溃。

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

term_sessions(id, name, owner_id, backend[local|tmux|ssh], command, argv, env,
              cwd, idle_timeout, max_life, status, instance_id, created_at, last_active)

instances(id, pid, host, started_at, heartbeat)          -- 多实例租约

audit_logs(id, user_id, term_session_id, event, payload, ip, ts)

settings(key, value)
```

> 命名约定：`auth_sessions` 指**登录态**，`term_sessions` 指**终端会话**，
> `instances` 指**服务进程租约**。
> 模式版本 3；旧库通过 `PRAGMA table_info` + `ALTER TABLE` 幂等迁移补齐新列，且索引
> 在列补齐之后创建（否则升级旧库会因列不存在而失败）。

## 6. CLI 命令树（已实现）

```
wsctl serve                     # 起控制面，默认配置开箱即用
wsctl serve --new "bash"        # 启动时顺带开一个会话
wsctl serve --backend tmux      # 默认使用 tmux 后端（跨重启恢复）
wsctl doctor                    # 环境与配置自检
wsctl session list|new|rename|kill|attach   # new 支持 --backend local|tmux|ssh
wsctl session record|record-stop|recording
wsctl connect [url] [-s id]     # 瘦客户端：本地 raw 终端直连
wsctl login|logout              # 缓存/清除服务端凭据
wsctl user add|list|del|passwd|role|disable|enable|totp
wsctl audit                     # 查看审计日志（admin）
wsctl config show|path|edit|set|reload
wsctl version / --version
```

> `config set` 写入配置文件（校验 TOML），`config reload` 让运行中的服务热加载。


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
fastapi  uvicorn[standard]  typer  rich
pydantic  pydantic-settings  argon2-cffi  python-multipart
pyotp  segno  websockets

[win] → pywinpty
[e2e] → playwright
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
| **M12** | 杂项：`settings` 表 + `term_sessions` 完整字段（含迁移）、Webhook、`config set`、主题/字体可配、移动端适配 |
| **M13** | 前端浏览器级验证：Playwright 无头 Chromium 跑通登录/终端/多标签/文件面板/分享+二维码/主题/只读分享 |
| **M14** | 可写分享（share writable） |
| **M15** | 录制前端回放 UI（vendored asciinema-player）+ 录制开关 |
| **M16** | 自定义快捷键（可编辑、本地持久化） |
| **M17** | 配置运行时热更新（文件监听 + 管理端点 + CLI） |
| **M18** | 每会话内存硬上限 + 每客户端字节上限 |
| **M19** | Sixel 图像渲染（addon-image）+ 可选 ZMODEM 传输（zmodem.js） |
| **M20** | 终端主题市场（10 内置主题 + 自定义 JSON 主题） |
| **M21** | 0.1.1 加固：多实例租约 + tmux 命名空间隔离、资源保留策略与每用户配额、attach 失败泄漏修复、schema 迁移顺序修复、限速器内存回收、全中文 UI、CLI 增强（doctor / --version / session rename / user disable|enable / config set 校验）、systemd 与 nginx 示例 |
| **M22** | 0.1.1 测试：实例租约/保留/配额/分享复用单测 + e2e `multiplex` 场景 + 浏览器 detach/分享复用用例 |
| **M23** | 0.1.2 异步化：审计写入队列 + 批量落盘（`core/audit.py`）、`last_seen` 节流、上传/录制移出事件循环、tmux 探测异步化、内存背压断连、审计缓冲上限、webhook 重试 |
| **M24** | 0.1.2 Web 管理面：用户管理（含 TOTP 二维码）、审计查看器（筛选）、录制管理；终端搜索、内联重命名、终止确认、Toast |
| **M25** | 0.1.2 便捷性：`config get/validate`、`doctor --json`、`backup`、终端内 TOTP 二维码、shell 补全、Docker 示例 |
| **M26** | 0.1.2 设计系统：令牌 + 组件、图标化工具栏 + 移动端折叠、统一 Modal、主题色预览、favicon/manifest、空态/加载态、a11y、跟随系统深浅色 |
| **M27** | 0.1.2 测试与文档：异步审计/管理 API/TOTP/节流/滑动 TTL 单测 + 浏览器用例扩展；README/DESIGN/CHANGELOG 同步 |

## 10.1 实现状态（截至 0.1.2）

**已实现**：M0–M20 全部交付项；二进制 WS 协议、会话与连接解耦、重连回放、
多用户 RBAC、审计 + `/api/audit`、登录限速、IP allowlist、TOTP、安全响应头、
文件面板（防穿越 + 上传限流）、`/metrics`、JSON 日志、`connect` 瘦客户端、
会话重命名、每会话连接上限、输入令牌桶限速、并发/大输出压测、可选 tmux 后端
（跨重启恢复）、只读/可写分享（share token + 二维码 + 匿名观看）、SSH 后端、
asciinema 录制 + 前端回放、SO_REUSEPORT 零停机重启、Webhook、
`settings` 表与完整 `term_sessions` 字段、主题/字体/快捷键可配 + 移动端适配、
`config set`/`reload`、前端浏览器级自动化测试、每会话内存硬上限、
Sixel 渲染、可选 ZMODEM、终端主题市场。

**尚未实现**：无。唯一验证缺口：ZMODEM 的真实 `rz`/`sz` 传输未在 CI 端到端跑通
（环境无 lrzsz），仅验证了集成不破坏常规终端 I/O。

> 0.1.1 起，关闭标签默认只断开连接（detach），终止会话需显式操作（右键菜单 /
> 会话列表 / `Alt+Shift+W`），与「会话独立于连接」的核心承诺一致。

> 0.1.2 起，磁盘/数据库写入全部移出事件循环：审计经 `AuditWriter` 队列批量落盘，
> 录制在后台线程写文件，上传在线程池落盘；`resolve_auth_session` 节流 `last_seen`。

> 回归测试：`scripts/e2e/run_all.py` 提供 7 个后端端到端场景（server / CLI /
> connect / tmux 重启恢复 / crash 崩溃收养 / **multiplex 多实例隔离** /
> SO_REUSEPORT 优雅重启）；`pytest -m browser` 为浏览器级测试；
> `pytest -m slow` 为并发/大输出压测。CI 在独立 job 中运行浏览器测试。

> 说明：进程回收依赖 `start_new_session` + `killpg` 与 `TermSession` 结束时的
> `wait()`，未安装全局 SIGCHLD handler（避免与 `subprocess` 争抢 PID）；已跟踪
> 会话不会残留僵尸进程。

## 11. Backlog（后续）

无（v0.1.2 计划项已全部落地）。后续可考虑：ZMODEM 真机端到端测试、
每用户后端策略（命令级权限隔离）、i18n 框架、更多终端协议（Kitty graphics 等）。

## 12. 发布

- 包名 / PyPI：`wsctl`（已验证可用）
- GitHub：`ThzxxArt/wsctl`
- 版本：语义化；初始 `0.1.0`
- CI：ruff + mypy + pytest（py3.11 / 3.12 / 3.13）
- 发布：tag 触发 PyPI Trusted Publishing（OIDC，无需存 token）
