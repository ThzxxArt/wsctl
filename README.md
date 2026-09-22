# wsctl

[![CI](https://github.com/ThzxxArt/wsctl/actions/workflows/ci.yml/badge.svg)](https://github.com/ThzxxArt/wsctl/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/wsctl.svg)](https://pypi.org/project/wsctl/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/ThzxxArt/wsctl/blob/main/LICENSE)

> 单机部署的 Web 在线终端。一条命令起服务，浏览器或命令行得到完整 shell；
> 终端**会话与连接解耦**——关掉笔记本不会杀掉 shell，重连即恢复屏幕。

`wsctl` 是 [ttyd](https://github.com/tsl0922/ttyd) 的现代 Python 超集：保留
「一条命令把终端搬上浏览器」的心智，并补齐**多会话、多用户 RBAC、审计、
分享、录制、文件面板与可观测性**，且 `pip install` 零编译。

```
浏览器 (xterm.js) ─┐
                   ├─ WebSocket ─┐
CLI (wsctl connect)┘             │
                       ┌─────────▼──────────┐        ┌──────────────┐
                       │   SessionManager    │──PTY──▶  bash / ssh   │
                       │  (asyncio, 1..N)    │        └──────────────┘
                       │  scrollback replay  │
                       └─────────────────────┘
```

## 目录

- [特性](#特性)
- [与 ttyd 的差异](#与-ttyd-的差异)
- [安装](#安装)
- [快速开始](#快速开始)
- [使用教程](#使用教程)
- [配置详解](#配置详解)
- [CLI 命令参考](#cli-命令参考)
- [部署](#部署)
- [安全](#安全)
- [常见问题与故障排查](#常见问题与故障排查)
- [开发](#开发)
- [许可](#许可)

## 特性

- **多会话**：一个服务托管多个终端，Web 端多标签切换。
- **会话与连接解耦**：客户端断开不影响会话；重连时回放 scrollback 重建屏幕。
  关闭标签默认只**断开连接**，会话仍在服务器上运行。
- **跨重启恢复（可选 tmux 后端）**：shell 跑在 tmux 里，服务重启后自动重新挂载。
- **多实例安全**：共享同一 `data_dir` 的多个实例通过租约互不干扰；`--reuse-port`
  升级时另一实例的活跃会话不会被误标或误杀。
- **多用户 + RBAC**：admin 管理全部，普通用户仅限自己的会话。
- **分享**：只读或可写分享链接，带二维码、可设有效期；已存在的链接会被复用而非
  静默作废。
- **CLI 瘦客户端**：`wsctl connect` 把本地终端桥接到远程服务。
- **SSH 会话**：会话直接是到远程主机的 `ssh` 连接。
- **文件面板**：在可配置根目录内浏览、下载、上传（含拖拽）。
- **录制与回放**：asciinema cast 录制，浏览器内回放。
- **终端能力**：Sixel 图像、可选 ZMODEM（`sz`/`rz`）传输。
- **主题与快捷键**：内置主题库 + 自定义主题；快捷键可编辑。
- **审计与 Webhook**：登录、会话、文件、管理操作全量审计，可推送到 Webhook。
- **可观测**：`/healthz`、Prometheus `/metrics`（可选要求认证）、结构化 JSON 日志。
- **资源有界**：审计日志、已结束会话与录制文件支持保留策略清理；支持每用户会话
  配额、每会话内存硬上限与背压。
- **零停机重启**：`SO_REUSEPORT` 让新实例先接管端口再停旧实例。
- **配置热更新**：大部分配置改动无需重启。
- **全中文界面**：Web UI 与 CLI 输出均为中文。

## 与 ttyd 的差异

| 维度 | ttyd | wsctl |
|---|---|---|
| 会话模型 | 一进程 = 一会话 = 一端口 | 单服务托管 N 个会话 |
| 多终端 | 多进程 / 依赖 tmux | 原生多会话 + 多标签 |
| 用户体系 | 单一 basic-auth 凭据 | 多用户 + RBAC |
| 审计 | 无 | 结构化审计日志 + Webhook |
| 连接语义 | 与进程耦合 | 解耦，重连回放屏幕 |
| 配置 | 命令行参数 | 配置文件 + CLI + 热更新 |
| 可观测 | 基础日志 | `/healthz` · `/metrics` · JSON 日志 |
| 语言 | C | Python |

**性能边界（诚实声明）**：`wsctl` 不在**原始吞吐**上对标 C + libuv 的 ttyd。
纯 Python 转发在 `cat` 大文件、`yes` 刷屏这类场景打不过 C。`wsctl` 的稳定来自
**架构**——会话与连接解耦、输出背压、每会话隔离——而非单连接速度。请勿做不公
平的 benchmark。

## 安装

### 环境要求

- Python **3.11 及以上**
- Linux（推荐）或 macOS；Windows 需额外安装 `pywinpty`
- 可选：`tmux`（跨重启恢复会话）、`ssh` 客户端（SSH 会话）

### 从 PyPI 安装

```bash
pip install wsctl

# 需要 Windows PTY 支持时
pip install "wsctl[win]"
```

### 从源码安装

```bash
git clone https://github.com/ThzxxArt/wsctl.git
cd wsctl
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"     # 含开发工具
```

### 可选依赖（extras）

| extra | 内容 | 用途 |
|---|---|---|
| `wsctl[win]` | `pywinpty` | Windows 下的 PTY 支持 |
| `wsctl[e2e]` | `playwright` | 浏览器级端到端测试 |
| `wsctl[dev]` | ruff / mypy / pytest 等 | 开发与测试 |

## 快速开始

```bash
wsctl serve
```

首次运行会在 `~/.local/share/wsctl/wsctl.db` 建库，并创建 `admin` 账号，随机
密码打印在**标准错误**上（也可用 `--admin-password` 指定）：

```bash
wsctl serve --admin-password '你的密码'
```

浏览器打开 <http://127.0.0.1:7681>，用 `admin` 登录，即得到一个 shell。

不想用浏览器？用命令行连上去：

```bash
wsctl login http://127.0.0.1:7681     # 交互式输入密码，凭据缓存到本地
wsctl connect                          # 在当前终端打开远程 shell
```

开始前可先做一次环境自检（Python 版本、数据目录、数据库、shell、tmux/ssh/lrzsz
是否可用、配置是否合法、服务器是否可达）：

```bash
wsctl doctor
wsctl --version
```

## 使用教程

### 1. 会话管理（Web 端）

- 顶部 **`+`**：新建会话（新标签）。
- 点击标签：切换会话。
- **双击标签**：重命名会话。
- 标签上的 **`×`**：**断开连接**（会话继续在服务器上运行，不会被杀掉）。
- **右键标签**：弹出菜单，可选择「关闭标签（保持会话）」或「终止会话」。
- 顶部 **`会话`**：打开会话列表，可重新打开已断开的会话，或终止任意会话。
- 断线后前端自动重连，并回放屏幕内容，无需重新登录。

> 默认快捷键：`Alt+N` 新建、`Alt+W` 断开标签、`Alt+Shift+W` 终止会话、
> `Alt+→/←` 切换标签、`Alt+L` 会话列表、`Alt+F` 文件、`Alt+S` 设置、`Alt+H` 分享。

### 2. 从命令行使用

```bash
# 登录并缓存 token（默认写到 ~/.config/wsctl/credentials.json）
wsctl login http://host:7681 -u admin

# 在本地终端里直接连远程 shell（Ctrl-D / exit 退出）
wsctl connect http://host:7681
wsctl connect -s <session-id>          # 连接到已有会话

# 管理会话
wsctl session list
wsctl session new --name build --command "bash -l"
wsctl session rename <session-id> 构建
wsctl session attach <session-id>
wsctl session kill <session-id>

wsctl logout                           # 清除本地缓存凭据
```

### 3. 会话后端：local / tmux / ssh

**local（默认）**：shell 是服务的直接子进程，服务退出即结束。

**tmux（跨重启恢复）**：shell 跑在 tmux 会话里，服务重启后自动重新挂载，屏幕
重绘。

```bash
wsctl serve --backend tmux
# 或针对单个会话
wsctl session new --backend tmux --command "htop"
```

需要在主机安装 `tmux`。优雅关闭会保留 tmux 会话（`tmux_preserve_on_shutdown`），
显式 kill / 空闲回收才会真正销毁。

**ssh（跳板）**：会话直接是到远程主机的 ssh 连接。

```bash
wsctl session new --ssh user@example.com
wsctl session new --ssh example.com --ssh-user root --ssh-port 2222 \
  --ssh-identity ~/.ssh/id_ed25519 --ssh-option StrictHostKeyChecking=accept-new
```

### 4. 文件面板

点击顶部 **files** 打开右侧面板：在配置的 `file_root`（默认当前用户家目录）内
浏览目录、点击下载、上传（按钮或拖拽）。

> 提醒：能开 shell 的账号本就能访问文件系统，文件面板不额外扩大权限面。

### 5. 分享会话

点击顶部 **分享**：

- 默认生成**只读**链接（含二维码），任何人打开即可观看但**不能输入**。
- 勾选「允许输入（可写）」生成**可写**链接。
- 可选择**有效期**（永久 / 10 分钟 / 1 小时 / 1 天），随时 **撤销分享**。
- 再次打开分享对话框会**复用**现有链接（不会作废已发出的链接）；修改选项后点
  「生成新链接」才会替换（旧链接随之失效）。

分享链接形如 `http://host:7681/?session=<id>&share=<token>`，观看者无需账号。

### 6. 录制与回放

```bash
wsctl session record <session-id>            # 开始录制（asciinema cast）
wsctl session record <session-id> --input    # 同时记录输入
wsctl session record-stop <session-id>
wsctl session recording <session-id> -o out.cast   # 下载
```

Web 端点击 **rec** 开始/停止，**replay** 在浏览器内用 asciinema 播放器回放。
可用 `auto_record = true` 让每个会话自动录制。

### 7. 终端能力：Sixel 与 ZMODEM

- **Sixel**：内嵌 `@xterm/addon-image`，远端输出 Sixel 图像即可直接显示
  （如 `img2sixel`、`lsix`）。
- **ZMODEM**：点击 **zmodem** 启用后，远端 `sz 文件` 会在浏览器下载，`rz` 会弹
  出上传选择框。默认关闭，避免影响常规 I/O。

### 8. 主题、字体与快捷键

点击顶部 **settings**：

- **主题**：内置 10 套终端主题（dracula、solarized、nord、gruvbox、monokai、
  one-dark、tokyo-night 等），也可粘贴 JSON 自定义。
- **字体大小**：12–18。
- **快捷键**（默认，可改）：新建 `Alt+N`、关闭 `Alt+W`、下一个标签
  `Alt+→`、上一个标签 `Alt+←`、文件 `Alt+F`、设置 `Alt+S`、分享 `Alt+H`。

偏好保存在浏览器本地。

### 9. 用户、权限与二次验证

```bash
wsctl user add alice                       # 交互式设置密码
wsctl user list
wsctl user role alice admin                # 提升为管理员
wsctl user passwd alice
wsctl user disable alice                   # 禁用（登录态立即失效）
wsctl user enable alice
wsctl user totp alice                      # 启用 TOTP 双因子（扫码确认）
wsctl user totp alice --disable
wsctl user del alice
```

- `admin`：可查看/管理所有会话与用户。
- `user`：仅能操作自己的会话。

> 注意：能登录并创建会话的账号，本质上就拥有以服务运行用户身份执行命令的能力。
> RBAC 控制的是**会话可见性与管理权限**，不是命令级隔离；请像对待 SSH 一样对待它。

### 10. 审计与 Webhook

所有敏感操作（登录成功/失败、限速、IP 拒绝、会话创建/连接/断开/终止、文件上传、
用户管理、配置重载）都会写入审计日志：

```bash
wsctl audit --limit 50          # 需管理员
```

也可通过 `webhook_url` 把每条审计事件以 JSON POST 到你的端点。

### 11. 可观测性

```bash
curl http://127.0.0.1:7681/healthz
curl http://127.0.0.1:7681/metrics      # Prometheus 文本格式
```

指标包含：运行状态、当前会话/客户端数、缓冲字节、会话创建数、WebSocket 连接数、
上传数、登录结果分类。开启 `log_json = true` 输出结构化 JSON 日志。若担心指标泄露，
可设 `metrics_require_auth = true` 要求登录后才能抓取 `/metrics`。

## 配置详解

优先级：**命令行参数 > `WSCTL_*` 环境变量 > 配置文件**。

配置文件默认位于 `~/.config/wsctl/config.toml`：

```toml
# ---- 网络 ----
host = "127.0.0.1"
port = 7681
reuse_port = false            # SO_REUSEPORT：新实例先接管端口再停旧实例

# ---- 认证与安全 ----
auth_required = true          # 是否强制登录（关闭仅供受信本地使用）
session_ttl = 43200           # 登录态有效期（秒）
cookie_secure = false         # 走 HTTPS 时置 true
trust_proxy = false           # 位于反向代理后时置 true，读取 X-Forwarded-For
allowed_origins = []          # WebSocket Origin 白名单（空 = 同源）
allowed_ips = []              # CIDR 白名单（空 = 允许全部）
security_headers = true       # nosniff / frame-deny / CSP 等响应头
login_rate_limit = 10         # 登录失败次数阈值
login_rate_window = 300       # 滑动窗口（秒）
audit_input = false           # 是否审计提交的命令行
totp_issuer = "wsctl"         # TOTP 认证器里的发行方名称

# ---- 会话默认值 ----
default_shell = "/bin/bash"   # 默认 shell（缺省用 $SHELL 或 /bin/bash）
default_cwd = "/home/me"      # 新会话工作目录
default_backend = "local"     # local / tmux
tmux_preserve_on_shutdown = true
idle_timeout = 3600           # 空闲多久回收会话（秒，可选）
max_life = 86400              # 会话最长寿命（秒，可选）
max_sessions = 64
max_sessions_per_user = 0     # 每用户会话上限（0 = 不限）
session_max_clients = 0       # 单会话最大客户端数（0 = 不限）
session_memory_limit = 67108864  # 单会话缓冲上限（字节）
client_max_bytes = 8388608    # 单客户端积压上限（字节）
input_rate_limit = 0          # 单连接输入速率（字节/秒，0 = 不限）
input_rate_burst = 0          # 令牌桶容量（默认等于速率）
scrollback_bytes = 4194304    # 回放缓冲上限（字节）

# ---- 文件面板 ----
file_root = "/home/me"        # 面板根目录（默认当前用户家目录）
file_max_upload = 104857600   # 单文件上传上限（字节）

# ---- 录制 ----
auto_record = false           # 自动录制每个会话
record_input = false          # 录制是否包含输入

# ---- 可观测 / 日志 ----
metrics_enabled = true
metrics_require_auth = false  # true 时抓取 /metrics 需登录
log_level = "info"
log_json = false
webhook_url = ""              # 审计事件 POST 目标（可选）

# ---- 多实例与保留策略 ----
instance_ttl = 30             # 其他实例租约失联多久视为已死（秒）
audit_retention_days = 30     # 审计日志保留天数（0 = 不清理）
term_session_retention_days = 30  # 已结束会话记录保留天数（0 = 不清理）
recordings_retention_days = 0     # 录制保留天数（0 = 不清理）
recordings_max_bytes = 0          # 录制目录总大小上限（0 = 不限）

# ---- 数据与 TLS ----
data_dir = "/var/lib/wsctl"   # 数据库与录制存放目录
ssl_cert = "/etc/wsctl/cert.pem"
ssl_key = "/etc/wsctl/key.pem"
```

每个键都有对应环境变量，例如 `WSCTL_PORT=9000`、`WSCTL_ALLOWED_IPS='["10.0.0.0/8"]'`。

### 修改与热更新

```bash
wsctl config show                       # 查看生效配置
wsctl config path                       # 配置文件路径
wsctl config edit                       # 用 $EDITOR 编辑（不存在则生成模板）
wsctl config set port 9000              # 写入配置（值须为合法 TOML）
wsctl config reload                     # 让运行中的服务重载配置
```

服务也会监听配置文件修改时间自动重载。可热更新的项包括白名单、限速、会话上限、
录制、文件根目录、指标开关等；**网络、TLS、数据目录、日志级别、Webhook 需重启**。

## CLI 命令参考

```
wsctl serve                     启动服务（--host/--port/--backend/--reuse-port/
                                --new/--ssl-cert/--ssl-key/--admin-password/--log-json）
wsctl doctor                    环境与配置自检（Python/数据目录/DB/shell/tmux/ssh/lrzsz/服务器）
wsctl --version                 显示版本
wsctl connect [URL] [-s ID]     把本地终端连接到服务
wsctl login URL                 登录并缓存凭据
wsctl logout                    清除本地缓存凭据
wsctl session list              列出会话
wsctl session new               新建会话（--name/--command/--cwd/--backend/--ssh…）
wsctl session rename ID NAME    重命名会话
wsctl session attach ID         连接到已有会话
wsctl session kill ID           终止会话
wsctl session record ID         开始录制（--input）
wsctl session record-stop ID    停止录制
wsctl session recording ID      下载录制（-o FILE）
wsctl user add|list|del|passwd|role|disable|enable|totp
wsctl audit                     查看审计日志（管理员）
wsctl config show|path|edit|set|reload
wsctl version
```

> `wsctl config set` 会校验配置项名称，未知项会报错并给出最接近的候选。

## 部署

### systemd

仓库内提供了可直接使用的示例：[`contrib/systemd/wsctl.service`](contrib/systemd/wsctl.service)。

```ini
[Unit]
Description=wsctl web terminal
After=network.target

[Service]
User=me
ExecStart=/usr/local/bin/wsctl serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### nginx 反向代理（TLS 终止）

完整示例见 [`contrib/nginx/wsctl.conf`](contrib/nginx/wsctl.conf)。关键点：

```nginx
location / {
    proxy_pass http://127.0.0.1:7681;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

位于代理后请设 `trust_proxy = true`，让限速与 IP 白名单看到真实客户端 IP；生产
环境请用 HTTPS/WSS 并设 `cookie_secure = true`。

### 零停机重启

`reuse_port = true`（或 `--reuse-port`）让监听 socket 带 `SO_REUSEPORT`，新实例
可在旧实例退出前绑定同一端口，升级无「连接被拒」窗口：

```bash
wsctl serve --reuse-port &      # 运行中的实例
# 部署新版本后：
wsctl serve --reuse-port &      # 新实例接管新连接
kill <旧进程 PID>               # 旧实例排空后退出
```

两个实例短暂共享同一 `data_dir` 是安全的：每个实例通过 `instances` 租约表与
`term_sessions.instance_id` 只管理自己的会话，绝不会把对方的活跃会话标记为停止；
tmux 会话也按 `data_dir` 命名空间隔离。配合 **tmux 后端**，连会话本身也能跨重启
存活。

## 安全

Web 终端本质上是**远程代码执行服务**，请像对待 SSH 一样对待它：

- 默认开启认证，密码使用 Argon2 哈希；会话 token 仅存哈希。
- 可选 TOTP 双因子（`wsctl user totp <用户>`）。
- 登录失败限速；`allowed_ips` 限制来源网段。
- WebSocket 握手校验 Origin 白名单（防 CSWSH）。
- 分享链接不可猜测、可设有效期、可撤销；只读链接拒绝输入。
- 文件面板防目录穿越（含符号链接），上传有大小限制。
- 每会话连接数/内存/输入速率上限，防止资源耗尽。
- 全量审计日志。
- 请务必使用 HTTPS/WSS，并以最小权限用户运行。

漏洞报告方式见 [SECURITY.md](https://github.com/ThzxxArt/wsctl/blob/main/SECURITY.md)。

## 常见问题与故障排查

**Q：首次运行没看到密码？**
密码打印在标准错误。若丢失，可用 `wsctl user passwd admin` 重设，或删除数据库后
重启重新初始化。

**Q：浏览器连不上 / 一直重连？**
- 检查反向代理是否转发 WebSocket（`Upgrade` / `Connection` 头）。
- 若被判定未授权（401/4401），重新登录；确认系统时间正确（TOTP 场景）。

**Q：关闭标签后 shell 会结束吗？**
不会。关闭标签默认只是**断开连接**，会话继续在服务器上运行。用标签右键菜单、
顶部「会话」列表，或 `Alt+Shift+W` 才能真正终止会话。

**Q：审计日志 / 数据库 / 录制文件会不会无限增长？**
有保留策略：`audit_retention_days`（默认 30）清理审计日志，
`term_session_retention_days`（默认 30）清理已结束的会话记录，
`recordings_retention_days` 与 `recordings_max_bytes` 约束录制文件（默认关闭，
设为 0 表示不清理）。

**Q：`wsctl connect` 报「no server URL」？**
先 `wsctl login <url>`，或显式传 URL；token 也可用 `WSCTL_TOKEN` 提供。

**Q：tmux 会话没有跨重启恢复？**
确认主机安装了 `tmux`，且 `default_backend = "tmux"`（或创建时 `--backend tmux`）。
无头环境建议保持 `TERM` 可用（wsctl 会为 tmux 会话默认设为 `xterm-256color`）。

**Q：`rz`/`sz` 没反应？**
需先在 Web 端点击 **zmodem** 启用；远端需装 `lrzsz`。

**Q：如何只在本机使用、不要登录？**
`wsctl serve --no-auth`（**不安全**，切勿暴露到网络）。

**Q：上传大文件失败？**
调整 `file_max_upload`；注意反向代理可能也有请求体大小限制。

## 开发

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && mypy src && pytest
```

浏览器级测试（Playwright，无头 Chromium）：

```bash
pip install -e ".[e2e]"
playwright install chromium
pytest -m browser
```

后端端到端场景（server / CLI / connect / tmux / 零停机重启）：

```bash
python scripts/e2e/run_all.py
```

压测（并发 / 大输出）：

```bash
pytest -m slow
```

架构与路线图见 [DESIGN.md](https://github.com/ThzxxArt/wsctl/blob/main/DESIGN.md)。

## 许可

[MIT](https://github.com/ThzxxArt/wsctl/blob/main/LICENSE) © 2026 ThzxxArt

内嵌的第三方前端资源（xterm.js、asciinema-player、zmodem.js）许可证见
[THIRD_PARTY_NOTICES.md](https://github.com/ThzxxArt/wsctl/blob/main/THIRD_PARTY_NOTICES.md)。
