# wsctl 设计文档

> 版本：0.1.23 · 状态：0.1.0–0.1.16、0.1.18–0.1.22 已发布（0.1.17 撤回，由 0.1.18 承载）
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
- **进程治理**：`start_new_session` + `killpg`，并在会话结束时 `wait()` 回收（**有界
  等待，超时强杀**：子进程若被守护化孙进程占住终端而不退，也不能挂死清理路径）；
  未安装全局 SIGCHLD handler（避免与 `subprocess` 争抢 PID）。已跟踪会话不残留僵尸进程。
- **分享也是承诺**：share token / 有效期 / 可写标记与会话一起持久化，服务重启后原样
  恢复（不轮换 token），已分发的二维码与链接继续可用；重命名会话不会使其失效。

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
              cwd, idle_timeout, max_life, status, instance_id,
              share_token, share_expires, share_writable,
              created_at, last_active)

instances(id, pid, host, started_at, heartbeat)          -- 多实例租约

audit_logs(id, user_id, term_session_id, event, payload, ip, ts)

settings(key, value)
```

> 命名约定：`auth_sessions` 指**登录态**，`term_sessions` 指**终端会话**，
> `instances` 指**服务进程租约**。
> 模式版本 4；旧库通过 `PRAGMA table_info` + `ALTER TABLE` 幂等迁移补齐新列，且索引
> 在列补齐之后创建（否则升级旧库会因列不存在而失败）。
>
> `share_token` / `share_expires` / `share_writable` 持久化分享链接：分享是**承诺**，
> 必须和会话一样跨重启存活（0.1.4 起）。降级到 0.1.3 及更早版本前请先 `wsctl backup`，
> 旧版本不认识这几列。

## 6. CLI 命令树（已实现）

```
wsctl serve                     # 前台起服务（--daemon 转后台）
wsctl start|stop|restart|status|logs|reload   # 非 systemd 后台生命周期
wsctl serve --new "bash"        # 启动时顺带开一个会话
wsctl serve --backend tmux      # 默认使用 tmux 后端（跨重启恢复）
wsctl doctor                    # 环境、配置、运行状态、端口、SO_REUSEPORT、磁盘、时钟自检
wsctl session list|new|rename|kill|attach   # 均支持 --json；new 支持 --backend local|tmux|ssh
wsctl session record|record-stop|recording
wsctl connect [url] [-s id]     # 瘦客户端：本地 raw 终端直连（断线自动重连）
wsctl login|logout              # login 支持 --totp / WSCTL_TOTP（两步验证账号）
wsctl user add|list|del|passwd|role|disable|enable|totp
wsctl audit [--event|--user-id|--ip|--json]
wsctl backup FILE [--include-config] / restore FILE [--force]
wsctl config show|get|path|edit|set|validate|reload    # set 保留行内注释
wsctl completion install|show [bash|zsh|fish|powershell]
wsctl version / --version
```

> `config set` 写入前会校验单项类型与整文件合法性（不合法则拒绝并回滚），并保留被改行
> 尾部的注释；`config reload`（或 `wsctl reload` 的 SIGHUP）让运行中的服务热加载，
> **配置写错时会原样打印错误并以非零码退出**，绝不静默忽略。
>
> **互斥/依赖强校验**：所有互相冲突或彼此依赖的参数在 `cli/validate.py` 中声明式
> 定义，非法组合在进程启动前即以退出码 2 报错，而不是静默忽略其一。
>
> **后台生命周期**（`cli/daemon.py`）：子进程以 `start_new_session` 脱离终端，
> stdout/stderr 重定向到 `<data_dir>/run/wsctl-<port>.log`（可按大小轮转，
> copytruncate 语义以保住继承的追加型描述符）；pid 文件记录
> **进程身份指纹**（Linux 取 `/proc/<pid>/stat` 的 starttime），`stop/status` 在
> 发信号前核对，避免 PID 复用误杀。`--reuse-port` 的多实例热切换不纳入托管。


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
| **M28** | 0.1.3 非 systemd 后台生命周期：`start/stop/restart/status/logs/reload`（`start --force` 先停再启）+ `serve --daemon`；pidfile（进程身份指纹）+ 日志文件 + stale-pid 清理；SIGHUP 配置重载；Windows 明确不支持 |
| **M29** | 0.1.3 参数互斥/依赖强校验：`cli/validate.py` 声明式规则（conflicts/requires/requires_if/choices）；`config set` 类型与整文件校验 + 回滚；`serve --backend`/`--ssl-*`/`--daemon`/`--reuse-port` 组合约束；`--reuse-port` 在平台不支持 `SO_REUSEPORT` 时**明确报错**而非静默降级 |
| **M29b** | 0.1.3 用户管理不变量同源：`core/user_admin.py`，CLI 与 API 共用（最后管理员保护、不能操作自身、改密/禁用即吊销登录态） |
| **M30** | 0.1.3 事件循环解阻：argon2 认证/建用户/改密移出循环、`Recorder.close` 非阻塞、保留清理/录制容量/目录列举入线程、AuditWriter 容错与 flush 串行化、维护滞后指标 |
| **M31** | 0.1.3 连接健壮性：WS 关闭码语义化（4400/4401/4403/4404/4408 空闲超时/4409/4500）+ 前端按码处理（永久失败不再重连、会话不存在不再误弹登录框）；**半开连接检测**（服务端 120s 无消息回收，前端与 CLI 每 25s 心跳）；`connect` 客户端指数退避自动重连；会话 id 改十六进制（避免以 `-` 开头被当选项） |
| **M32** | 0.1.3 一致性备份/恢复：`backup` 用 SQLite 在线备份 API（含 WAL）、`restore` 校验归档/拒绝穿越/`--force` 保留 `.bak`；`status --json`；`doctor` 扩展运行状态/端口/reuse-port/后台能力 |
| **M33** | 0.1.3 前端易用性：密码掩码对话框、QR 弹窗纳入统一 Modal（Esc 关最上层/焦点陷阱）、快捷键冲突检测 |
| **M34** | 0.1.3 测试与文档：校验层/daemon 生命周期/备份恢复/用户不变量单测 + 关闭码测试 + e2e `daemon` 场景 + 浏览器 QR 用例；README/DESIGN/CHANGELOG 同步 |
| **M35** | 0.1.4 「说到做到」：CLI TOTP 登录（`--totp`/`WSCTL_TOTP`/交互补录）、密码策略（非空+≥8 位，`core/passwords` 单一入口，Store/user_admin 双闸，不强制改存量）、`_finalize` 有界等待 + 强杀（子进程不退也不再挂死）、**分享 token 持久化**（schema v4 + 收养回填 + 过期清理）、录制诚实性（写失败自闭 + `close` 与写线程互斥）、配置热加载报错（`(changed, errors)` 贯通 API/CLI/日志）、Argon2 惰性化 |
| **M36** | 0.1.4 「长跑不塌」：认证短缓存（`core/authcache.py`，改密/禁用/改角色即时失效）+ 同步 SQLite 全面出事件循环（WS 握手与周期复查、维护循环批量写、audit/users 查询）、PTY 写缓冲满**反馈**（一次性提示 + `wsctl_pty_input_dropped_total`）、日志轮转（copytruncate 保住继承的追加型 fd）、Web 惰性连接（最多 8 个在线，其余「未连接」按需挂载）、上传防覆盖（409 + 确认后 `overwrite=1`）、`list_dir` 上限 + 截断标记、`connect` 非 JSON 文本帧容错、metrics 标签转义、scrollback UTF-8 边界对齐 |
| **M37** | 0.1.4 「脚本与界面顺手」：CLI 全线 `--json`（session/user/audit/config show）+ `audit` 筛选 + `session list` 显示用户名/后端/存活、Web 建会话对话框（名称/命令/工作目录/后端/结构化 SSH 表单 + 必填校验）、Web 管理面「配置」页签（`GET /api/config`，标注热更新/需重启 + 重载反馈）、`config set` 保留行内注释且不误改注释行、`wsctl completion install|show`（bash/zsh/fish/powershell，按 `$SHELL` 探测）、`doctor` 增检磁盘/密码策略/日志/系统时钟、CLI 冷启动懒加载（`wsctl version` 1.05s → 0.36s） |
| **M38** | 0.1.4 部署与工程化：Docker 改**仓库根构建**（装本地源码而非 PyPI 旧版）+ `entrypoint.sh` 首启建号 + compose 强制 `WSCTL_ADMIN_PASSWORD` + `.dockerignore`、CI 加 macOS 腿与 Windows 导入/CLI 冒烟、`-m slow` 压测入 CI、装 `lrzsz` 真机验证 ZMODEM 收发、构建 Docker 镜像冒烟、systemd 单元注明 `ExecStart` 路径来源、e2e 失败时打印子进程日志 |
| **M39** | 0.1.4 测试与文档：新增密码策略/认证缓存/日志轮转/录制失败/UTF-8/metrics 转义六个套件，扩展分享持久化、有界 `Pty.wait`、PTY 丢输入、上传覆盖、配置端点与重载报错、TOTP/`--json`/注释保留/补全；浏览器覆盖建会话对话框、配置页签、上传覆盖提示、连接预算、**ZMODEM `sz`/`rz` 字节级真机往返**；e2e 断言分享跨重启存活；README/DESIGN/CHANGELOG/SECURITY 同步 |
| **M40** | 0.1.5 Windows 可用性与跨平台收敛：`signal.SIGHUP` 默认参数不再在定义期求值（此前 Windows 一 import 就炸，`DEFAULT_TERM_SIGNAL` 回落 `SIGTERM`）、CLI 启动时把 stdio 切到 UTF-8 + `errors="replace"`（此前 cp1252 控制台编不了中文，`wsctl doctor` 直接崩）、`fs.relative_to` 恒用 POSIX 分隔符（审计/分享路径跨平台可对齐）；测试改为封闭与确定性：CLI 用例不依赖开发者本机 `credentials.json`、PTY 丢输入用例 stub 掉内核缓冲而非假设其尺寸、浏览器断言改为结果导向并做空值守卫；CI 的 Windows job 装齐 `dev` extras 并用 `python -m pytest` |
| **M41** | 0.1.6 运维可用性：生命周期命令**自动跟随正在运行的实例**（未指定 `--port` 时实例优先于默认端口，多实例则列出待选；`restart` 把实际 host:port 注入子进程 argv）、`--admin-password` 不再静默忽略（终端告警 + 无启用管理员时用该值救场 + `user passwd -p` 一条命令自救）、**回环地址不走 `http_proxy`**（`core/net.opener_for`，否则本地实例被代理回 502 且拖累全部 ApiClient 命令）、`doctor` 报真实监听地址并列出发现的其他实例 |
| **M43** | 「故障不静默」：输入限速改为**丢弃并提示**而非断开连接（连续 3 次超限才用专用码 4429 断开）；关闭码收口到 `core/closecodes.py` 单一定义（浏览器镜像同一份并由测试断言相等）；内存驱逐先发 `evicted` 再断；运营提示改走 Toast，不再写进终端缓冲（避免重连回放时重现）|
| **M44** | 「声明与行为一致」：热重载字段拆成 `立即生效` / `新建时生效` / `需重启` 三档（八项此前标注不准确），每个配置项必须被归类且只归一类；`session_sliding_ttl` 热重载真正接到 `Store.sliding_ttl`；会话列表 owner 批量查询并出事件循环；`term_session_upsert`/`set_status` 移到工作线程；上传改为侧车 + `os.replace` 原子写入；`doctor` 用只读连接探测数据库（不再创建它）；回放缓冲按块发送 |
| **M45** | 文件面板补全：新建目录 / 重命名 / 删除 / 右键上下文菜单 / 按名称过滤 / 分页（取代 2000 条硬截断）/ 文本预览与就地编辑 / 拖放到具体目录 / 上传进度条与可取消。递归删除**刻意不提供** |
| **M46** | 状态可见：`GET /api/sessions/history` 与 `/api/sessions/{sid}/detail`（此前 `term_sessions` 写了 `status` 与保留策略却无人读取，已结束的会话在产品里凭空消失）；Web 会话弹窗分「运行中 / 已结束」；管理员「概览」页签（实例、统计、最近事件）|
| **M47** | 界面顺手：`?` 快捷键帮助浮层；终端搜索三开关（大小写/全词/正则）+ 无效正则明确报错 + 修 `translateToString` 吃行尾空格；`api()` 15s 超时与错误分类；Toast 可关闭、错误常驻；管理面板重绘保留滚动与焦点；会话重命名广播给所有已连接客户端；a11y（`prefers-reduced-motion`、触控目标 ≥44px）|
| **M48** | 工程底座：`create_app` 942 行 → 358 行。30 个路由移入 `server/routes/`（10 个关注点模块）、请求体进 `models.py`、DI 进 `deps.py`、跨实例对账进 `maintenance.py`。**行为零变化**，拆分前后全量回归对拍 |
| **M49** | 测试与文档：新增配置三档分类不变量、关闭码三方一致、上传原子性、doctor 无副作用、文件管理 API、会话历史/详情/概览、重命名广播、快捷键浮层与搜索三开关等用例；README/DESIGN/CHANGELOG/SECURITY 同步 |
| **M50** | 「能重启自身」：`wsctl stop`/`restart` 检测是否运行在被管理会话内（沿 `/proc` ppid 链上溯），默认**拒绝**并给出两条出路；`stop` 未运行时幂等退出 0（`stop && start` 不再被 `&&` 静默截断）；`stop` 找错端口时首句给可复制的修正命令；新增 **`restart --rolling`**（先起后停）——`/healthz` 自报 pid 让闸门认得继任者身份；解除 `daemon`/`reuse-port` 互斥使零停机可在托管生命周期内使用 |
| **M51** | 终端**右键菜单**（复制/粘贴/全选/查找/清屏/字号±/ZMODEM/断开/终止）+ 右键行为三模式（菜单 / 快捷复制粘贴 / 始终粘贴）|
| **M52** | 快捷键改 `Alt+C`/`Alt+V`（`Ctrl+Shift+C` 被浏览器 DevTools 占用，页面拦不住）+ `Ctrl/Shift+Insert` 传统通道 + 帮助浮层**可靠性图例**（✅/⚠️/❌）+ `?` 不再抢终端输入（打字时 `?` 到达 shell，`Alt+?` 随时唤帮助）|
| **M53** | 停止/回收/终止前先广播原因（`session.stop(reason=)`），界面不再是无预警黑屏 |
| **M54** | e2e 新增 `selfrestart` 场景（会话内执行 stop 被拒 / stop 幂等 / 零停机滚动重启 / 受控迁移后零停机）；e2e 环境剥离继承的 `WSCTL_*`（开发者 shell 泄漏曾让双 `--reuse-port` 实例误抢 pidfile）|
| **M42** | 0.1.7 `doctor` 补齐实例语义：`--host`/`--port` 收窄选实例（与 status/logs/stop 同源），「服务器」项改为**探测本机实例**而非 `wsctl login` 的陈旧缓存地址（此前把健康机器报成失败），回退到缓存地址时标明来源与 `wsctl logout` 清理方式；**测试封闭性结构性根治**——autouse fixture 默认隔离 `WSCTL_*` 与 `XDG_CONFIG_HOME`，杜绝同类第三次复发（含 `tests/test_isolation.py` 金丝雀：关闭隔离则红、开启则绿）|



## 10.1 实现状态（截至 0.1.23）

**已实现**：M0–M20 全部交付项；二进制 WS 协议、会话与连接解耦、重连回放、
多用户 RBAC、审计 + `/api/audit`、登录限速、IP allowlist、TOTP、安全响应头、
文件面板（防穿越 + 上传限流）、`/metrics`、JSON 日志、`connect` 瘦客户端、
会话重命名、每会话连接上限、输入令牌桶限速、并发/大输出压测、可选 tmux 后端
（跨重启恢复）、只读/可写分享（share token + 二维码 + 匿名观看）、SSH 后端、
asciinema 录制 + 前端回放、SO_REUSEPORT 零停机重启、Webhook、
`settings` 表与完整 `term_sessions` 字段、主题/字体/快捷键可配 + 移动端适配、
`config set`/`reload`、前端浏览器级自动化测试、每会话内存硬上限、
Sixel 渲染、可选 ZMODEM、终端主题市场。

**0.1.3 新增**：非 systemd 后台生命周期（pidfile + 身份指纹 + 日志文件 + SIGHUP
重载）、声明式参数互斥/依赖强校验、CLI 与 API 同源的用户管理不变量、一致性
备份/恢复（SQLite 在线备份）、WS 关闭码语义化、`connect` 自动重连、事件循环
解阻（argon2/录制/保留/审计容错）。

**0.1.7 新增**：`doctor` 补齐实例语义，并把**测试封闭性**从逐个打补丁改为结构性隔离（autouse fixture）。同类缺陷此前已造成三次「假绿」，根治比再补一次更划算。

**0.1.6 新增**：运维可用性。一条 bug 报告背后是四个独立缺陷——生命周期命令盯着默认端口而无视正在运行的实例、`--admin-password` 在库已有用户时被静默吞掉（表现为「用户名或密码错误」）、本地请求被 `http_proxy` 劫持返回 502、`doctor` 报默认地址。修复遵循同一原则：**命令应当作用于用户正在用的那个东西，并且拒绝时必须说清原因**。

**0.1.5 新增**：Windows 可用性与跨平台收敛。此前 `import wsctl.core.pty` 在 Windows 直接抛 `AttributeError: module 'signal' has no attribute 'SIGHUP'`（注解默认值在定义期求值），且默认 cp1252 控制台下 `wsctl doctor` 因中文输出抛 `UnicodeEncodeError`——**Windows 上 CLI 此前完全不可用**。两条均已根治，并把审计/分享里的相对路径统一为 POSIX 分隔符。测试侧同步收敛为封闭且确定（不再依赖开发者本机凭证、不再假设内核 PTY 缓冲尺寸）。

**0.1.4 新增**：承诺闭环与可靠性收口——**分享链接跨重启存活**（与会话同级的承诺）、
CLI 两步验证登录、密码策略、子进程不退也不挂死、录制失败自诚实、配置写错必报错、
认证查询出事件循环 + 短缓存、日志轮转、上传防覆盖、Web 连接预算与 SSH 建会话表单、
CLI 全线 `--json`、多 shell 补全。**验证深度提升**：ZMODEM 从「集成不破坏 I/O」升级为
**`sz`/`rz` 字节级真机往返**。

**0.1.8 新增**：主题「状态可见、操作完整、故障不静默」。这一版修的六处问题同属
一类——**系统知道，但用户看不见 / 行为与声明不符**，与 0.1.7 那条「服务器失败」
假警报同源。具体见 CHANGELOG：输入限速不再伪装成死终端、关闭码单一来源、
「热更新」三档（八项此前标注不准确）、会话历史终于可见、文件面板从三个动词补成
完整文件管理、运营提示不再污染终端缓冲。

**0.1.22 新增**：主题「检查跟着事实走，操作原子且不越权」。一次全量深度审计清账：
WS 授权复查改为**时钟驱动**（原先挂在「receive 超时」分支，持续发帧即可永久绕过禁用/
吊销/撤销）；滑动 TTL 窗口不再复利膨胀；rename / 上传暂存改为原子不可覆盖与唯一暂存名；
symlink 守卫因 `resolve()` 抹掉链接身份而成死代码，改为词法判定；copytruncate 轮转
补齐 copy→truncate 之间的丢行窗口；`trust_proxy` 的 XFF 改取最右非本机跳；TOTP 启用
两步确认（与 CLI 对齐）；ssh `-o` 收白名单（`ProxyCommand` 等可在本机执行命令）；
会话结束原因含退出码如实落库；12 处「测不到修复的守卫」一并重做（含两处生产路径零守卫、
回显假绿 20+ 处换算术标记）。

**0.1.18 新增**（0.1.17 撤回后由本号承载）：主题「连接生命周期收口」。`断开 → 重连 → 回放 → 失步` 环路上
最后一批**「动作做了、声明没跟上」**的缺口：scrollback `_trim` 在**零压力**下就
丢掉跨帧序列的前半（回放以 `1m` 开头的花屏，且守卫是弱形式、错代码照绿）；
`connect` 不清退避定时器导致**双 WebSocket 孤儿**；resync 写锁把重连回放也丢掉
（白屏）；失步徽标 / `resynced` / `evicted` 三处状态与代码的下一步互相矛盾。
根治方式与 0.1.8 同构——**每个状态声明都必须能从「刚发生的事实」推出来**，
并以 9 项突变体反向验证证毕。流畅性（搜索 debounce / write 封顶 / 粘贴分片）与
可用性（重连可见可打断 / 编辑脏检查 / 快捷键可靠性单一事实源）同版收尾。

**尚未实现**：无。验证边界（诚实声明）：
- ZMODEM 真机往返需要 `lrzsz`（CI 已装并执行；本地缺失时该浏览器用例跳过）。
- 后台生命周期为 POSIX-only；Windows 下 `serve --daemon`/`start` 明确报错并建议
  服务管理器，CI 仅做导入与 CLI 冒烟（`wsctl[win]` 的 `WinPty` 未端到端验证）。
- Docker 镜像构建在 CI 的 `docker` job 中验证；本机无 docker 时未构建过。

> 0.1.1 起，关闭标签默认只断开连接（detach），终止会话需显式操作（右键菜单 /
> 会话列表 / `Alt+Shift+W`），与「会话独立于连接」的核心承诺一致。

> 0.1.2 起，磁盘/数据库写入全部移出事件循环：审计经 `AuditWriter` 队列批量落盘，
> 录制在后台线程写文件，上传在线程池落盘；`resolve_auth_session` 节流 `last_seen`。

> 0.1.4 起，**读路径**同样出事件循环：WS 握手与周期复查、维护循环的租约/回收/保留、
> 审计与用户查询均在线程池执行；`resolve_auth_session` 前置一层短缓存
> （`core/authcache.py`），改密/禁用/改角色/删除时主动失效对应条目，因此
> 「禁用用户立即踢线」的语义不变。

> 回归测试：`scripts/e2e/run_all.py` 提供 8 个后端端到端场景（server / CLI /
> connect / tmux 重启恢复 **含分享跨重启存活断言** / crash 崩溃收养 /
> **multiplex 多实例隔离** / **daemon 后台生命周期** / SO_REUSEPORT 优雅重启）；
> `pytest -m browser` 为浏览器级测试（含 ZMODEM 真机往返）；`pytest -m slow`
> 为并发/大输出压测。CI 在独立 job 中运行浏览器测试、慢速压测与 Docker 构建。

> 说明：进程回收依赖 `start_new_session` + `killpg` 与 `TermSession` 结束时的
> `wait()`（**有界等待，超时强杀**），未安装全局 SIGCHLD handler（避免与
> `subprocess` 争抢 PID）；已跟踪会话不会残留僵尸进程。

| **M55** | 渲染三件套：`@xterm/addon-webgl` 0.18.0（主）→ `@xterm/addon-canvas` 0.7.0（无 GPU 兜底）→ DOM 渲染器，三级探测逐级降级；`@xterm/addon-unicode11` 0.8.0 宽度表 + CJK 等宽字体栈；设置里新增渲染器实况 |
| **M56** | 前端帧合批：`onmessage` 入队、每动画帧合并一次 `term.write`；合批次数经心跳回传为 `wsctl_ws_frames_coalesced_total` |
| **M57** | 尺寸合流：`window.resize` / `term.onResize` 双向 debounce 120ms 且尺寸未变不发；`applyPrefs` 只重排活动会话 |
| **M58** | 服务端丢帧 O(1)：出站拆「字节帧 + 控制帧」双 deque 带单调序号、`run()` 归并出队保序；淘汰从 O(n) 扫描变 `popleft()`；`_broadcast` 投递移出锁外（快照在锁内，顺序保证不变） |
| **M59** | 花屏三修：ZMODEM 传输中禁用开关（协议字节不再被当文本渲染）；失步徽标 + 一键重新同步（`desync` / `resync` / `resynced`，重放期间丢弃重复帧）；标签切换同步 `fit()` 消除空白帧 |
| **M60** | Toast 体系重做：四档定时消失（错误 10s）、悬停暂停、最多 4 条折叠、同类合并计数、`role=alert` |
| **M61** | 弹框设计系统 v2：语义尺寸 `sm/md/lg/xl`、按钮四档（`primary`/`ghost`/`warn-btn`/`danger` 仅限不可逆）、字段分组卡片、两列表单网格、统一焦点环 |
| **M62** | 管理弹框单一事实源：`adminTab` 驱动高亮，默认「概览」；选择器收窄 `#admin-overlay .tab2`（连带修掉误清会话弹窗高亮） |
| **M63** | 设置弹框 560px + 四组卡片重排 + 快捷键两列网格 |
| **M64** | 列表事件委托（每容器 1 监听替代每行 3-4 个）+ 筛选 debounce 250ms |
| **M65** | 性能验收数值门入 `-m slow`：T1 `seq 1 200000` ≤ 8s · T2 8 会话并发 ≤ 20s 且零驱逐 · T3 事件循环延迟 < 50ms；完成标记用算术展开防回显误匹配 |
| **M66** | 观测补齐：`wsctl_event_loop_lag_seconds` / `_peak`、`wsctl_ws_frames_coalesced_total`、`wsctl_shed_resync_requests_total`；`window.__wsctlScreen()` 渲染器无关读屏 |
| **M67** | scrollback 回放起点收口：`_trim` 仅在超容时按 WsClient 语义切割（丢到「最后一块结束在序列外」）；`_inside` 升级为状态串 + `AnsiTracker.resume`/`skip_to_boundary` 处理「保留块从序列中间开始」；单块尾切真正对齐（`_align_ansi` 空转删除）；守卫升级为**强形式**（被淘汰前缀喂 tracker）+ 零压力跨帧回归 |
| **M68** | 连接状态机收口：`resetTransients`（退避/写队列/resync 锁为连接级瞬态）+ 全 ws 处理器代际比对（`s.ws === ws`）+ 替换前关旧 socket + `onclose` 置空 `s.ws`——重连不再双开、回放不掺旧帧 |
| **M69** | 失步状态随事实复位：`onopen`/`attached` 抬 resync 写锁（attach 回放是 resync 的超集）；`attached` 在本连接无 `desync` 时收起徽标（scrollback 完整、回放已修复）；`resynced` 带 `incomplete`（resync 自己丢帧时不宣布成功）；`evicted` 只 toast，连接指示由 `onclose` 唯一驱动 |
| **M70** | 流畅性收尾：搜索 debounce 250ms（Enter 立即）；单次 `term.write` 封顶 256 KiB；大段粘贴按 32 KiB 分片（WS 保序，PTY 写缓冲不被整包占满） |
| **M71** | 可用性收尾：退避中显示「重连中（Ns 后重试）」+ 点击立即重连；预览/编辑关闭前脏检查（`warn` 确认）；文件刷新单次注册 |
| **M72** | 快捷键可靠性单一事实源：`chordGrade()` 统一 `BROWSER_RESERVED`（全小写、查找不区分大小写）与 `CHORD_RELIABILITY`；`Alt+←/→` 实测可被 `preventDefault` 接管后移出保留集、保留为默认标签切换键，`test_browser_alt_arrow_really_switches_tabs` 为证据 |
| **M73** | 回放帧合批（用户现场缺陷）：`Scrollback.replay_frames` 将数千 PTY 小读合批为 ~64 KiB 线帧，attach / resync 不再被 `MAX_PENDING` 帧数上限绞碎自身回放；`attached`/`resynced` 带 `incomplete`（回放丢帧不许宣布成功）；CLI `connect` 同步可见 `desync`/`notice`/`evicted`/`incomplete` |
| **M74** | 回归 review 收口：`flushWrite` 入口 disarm 旧定时器（直接调用不再留孤儿）；`requestResync` 清队后断管；`paintConnectionFor` 唯一写入状态栏附属物；`chordGrade` 空绑定不评级；`AnsiTracker.resume`/`skip_to_boundary` 直接单测 |
| **M75** | 发布前终检：`__version__` 与 `pyproject.toml` 单一事实源对齐（此前分叉、`wsctl --version` 报旧版）；「write 封顶 256 KiB」「点击立即重连」补结构契约——CHANGELOG 的每条声明都必须有能变红的守卫 |
| **M76** | 回放合批帧宽服从客户端字节预算（`replay_target_for` = `min(64 KiB, max_bytes)`）：固定 64 KiB 帧在小预算下整帧被丢、整段回放消失的自引入回退；browser 丢帧触发改确定性（单次大写入 vs 小预算，不再依赖洪峰竞速） |
| **M77** | **恢复模型换轨：字节流回放 → 应用自绘。** 字节流回放对增量绘制的 TUI 永远还原不了屏幕（有洞/无头即死局，resync 重放同一段 = 复现同一片花屏）；`TermSession.nudge_repaint()` 以两个真实 SIGWINCH 让应用从自身模型重绘，attach（有回放时）与 resync 后自动触发，服务端发起（只读观众/CLI 同享）。失步提示条删除 Ctrl+L 的错误建议 |
| **M78** | **scrollback 保留语义修正：整块淘汰只在完全多余时进行，余量尾切**——环形缓冲留「最新 N 字节」而非「整块扔到不超容」（只超 50B 却扔 5000B 块的过冲，使回放内容取决于 PTY 读边界的彩票）；三布局对拍守卫钉死读边界不变性 |

## 11. Backlog（后续）

无（0.1.8 计划项已全部落地）。0.1.8 明确**不做**的边界（诚实声明）：终端分屏/网格
布局、跨会话全局搜索、PWA Service Worker、API 长期密钥、配置在线写入、图片/Office
预览、文件版本、文件夹分享、Kitty graphics、i18n 框架、Windows 服务封装与 `WinPty`
端到端验证、`--reuse-port` 实例的托管。这些留待后续版本。

> 文件面板的边界：只提供**文件管理 + 文本预览/编辑**。递归删除刻意不提供——
> 一次误点不能清空家目录，空目录需先进入逐项删除，这是两次刻意动作。

## 12. 发布

- 包名 / PyPI：`wsctl`（已验证可用）
- GitHub：`ThzxxArt/wsctl`
- 版本：语义化；初始 `0.1.0`
- CI：ruff + mypy + pytest（py3.11 / 3.12 / 3.13）
- 发布：tag 触发 PyPI Trusted Publishing（OIDC，无需存 token）
