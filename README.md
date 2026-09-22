# wsctl

[![CI](https://github.com/ThzxxArt/wsctl/actions/workflows/ci.yml/badge.svg)](https://github.com/ThzxxArt/wsctl/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/wsctl.svg)](https://pypi.org/project/wsctl/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/ThzxxArt/wsctl/blob/main/LICENSE)

> **单机部署的 Web 在线终端。** 一条命令起服务，浏览器或命令行得到完整 shell；
> 终端**会话与连接解耦**——关掉笔记本不会杀掉 shell，重连即恢复屏幕。

`wsctl` 是 [ttyd](https://github.com/tsl0922/ttyd) 的现代 Python 超集：保留
「一条命令把终端搬上浏览器」的心智，并补齐**多会话、多用户 RBAC、审计、分享、
录制、文件面板与可观测性**，`pip install` 零编译。

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

- [wsctl 是什么](#wsctl-是什么)
- [界面截图](#界面截图)
- [环境要求](#环境要求)
- [安装](#安装)
- [升级](#升级)
- [卸载](#卸载)
- [快速开始](#快速开始)
- [使用教程](#使用教程)
- [后台运行（非 systemd）](#后台运行非-systemd)
- [配置详解](#配置详解)
- [CLI 命令参考](#cli-命令参考)
- [部署](#部署)
- [安全](#安全)
- [常见问题与故障排查](#常见问题与故障排查)
- [开发](#开发)
- [许可](#许可)

## wsctl 是什么

一个进程托管 N 个终端会话，浏览器多标签或 CLI 均可接入；会话独立于连接，断线
重连即回放屏幕。

**核心能力**

- **多会话 / 多标签**：一个服务托管多个终端，Web 端标签切换，CLI 可枚举与连接。
- **会话与连接解耦**：客户端断开不影响会话；关闭标签默认只**断开连接**。
- **连接自愈**：前端与 `wsctl connect` 断线自动重连并回放屏幕；服务端回收半开连接。
- **跨重启恢复（可选 tmux 后端）**：shell 跑在 tmux 里，服务重启后自动重新挂载。
- **多用户 + RBAC**：`admin` 管理全部；普通用户仅限自己的会话。
- **分享**：只读/可写分享链接，带二维码、可设有效期、可撤销，**跨服务重启依然有效**。
- **SSH 会话**：会话直接是到远程主机的 `ssh` 连接。
- **文件面板**：在可配置根目录内浏览、下载、上传（含拖拽）。
- **录制与回放**：asciinema cast 录制，浏览器内回放。
- **终端能力**：Sixel 图像、可选 ZMODEM（`sz`/`rz`）传输、终端搜索。
- **审计与 Webhook**：登录、会话、文件、管理操作全量审计，可推送 Webhook。
- **可观测**：`/healthz`、Prometheus `/metrics`、结构化 JSON 日志。
- **资源有界**：保留策略、每用户会话配额、每会话内存上限与背压。
- **后台生命周期**：`wsctl start/stop/restart/status/logs/reload`，无需 systemd。
- **零停机重启**：`SO_REUSEPORT` 让新实例先接管端口再停旧实例。
- **配置热更新**：大部分配置改动无需重启，`wsctl reload` 可主动触发。
- **参数强校验**：互斥/依赖参数在启动前报错，绝不静默忽略其一。
- **配置写错必报错**：热重载遇到非法值会原样打印错误并退出非零，绝不静默吞掉。
- **密码策略**：新建/改密强制非空且至少 8 位（存量密码不强制修改，`doctor` 会提示）。
- **Web 管理面**：浏览器内管理用户（含 TOTP 二维码）、审计日志、录制文件。
- **全中文界面**：Web UI 与 CLI 输出均为中文。

**与 ttyd 的差异**

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

> **性能边界（诚实声明）**：`wsctl` 不在**原始吞吐**上对标 C + libuv 的 ttyd。
> 纯 Python 转发在 `cat` 大文件、`yes` 刷屏这类场景打不过 C。`wsctl` 的稳定来自
> **架构**——会话与连接解耦、输出背压、每会话隔离——而非单连接速度。

## 界面截图

| 登录 | 终端 | 文件面板 |
|---|---|---|
| ![登录](https://raw.githubusercontent.com/ThzxxArt/wsctl/main/docs/screenshots/01-login.png) | ![终端](https://raw.githubusercontent.com/ThzxxArt/wsctl/main/docs/screenshots/02-terminal.png) | ![文件](https://raw.githubusercontent.com/ThzxxArt/wsctl/main/docs/screenshots/03-files.png) |

| 会话列表 | 管理 · 用户 | 管理 · 审计 |
|---|---|---|
| ![会话](https://raw.githubusercontent.com/ThzxxArt/wsctl/main/docs/screenshots/04-sessions.png) | ![用户](https://raw.githubusercontent.com/ThzxxArt/wsctl/main/docs/screenshots/05-admin-users.png) | ![审计](https://raw.githubusercontent.com/ThzxxArt/wsctl/main/docs/screenshots/06-admin-audit.png) |

![设置](https://raw.githubusercontent.com/ThzxxArt/wsctl/main/docs/screenshots/07-settings.png)

前端零构建（内嵌 xterm.js），并有一套轻量设计系统：设计令牌、统一 Modal（Esc/遮罩
关闭 + 焦点陷阱）、Toast、图标化工具栏（窄屏折叠为「更多」）、主题色预览、空态与
骨架屏、跟随系统的深/浅色主题、`:focus-visible` 焦点环与 `aria-label`。

## 环境要求

| 项 | 要求 |
|---|---|
| Python | **3.11 及以上**（3.11 / 3.12 / 3.13 均已在 CI 验证） |
| 操作系统 | Linux（推荐）或 macOS；Windows 需 `wsctl[win]` |
| 可选组件 | `tmux`（跨重启恢复）、`ssh` 客户端（SSH 会话）、`lrzsz`（ZMODEM） |

> 没有 3.11+？可先用 [uv](https://docs.astral.sh/uv/) 安装独立 Python：
> `uv python install 3.13`。**切勿删除发行版自带的 Python**（apt 等系统组件依赖它）。

## 安装

推荐用 **pipx** 或 **uv** 安装到隔离环境，避免污染系统 Python；想隔离到自建 venv
也可用方式三。

### 方式一：pipx（推荐）

```bash
# 安装 pipx（Debian/Ubuntu）
sudo apt update && sudo apt install -y pipx
# 或：python3 -m pip install --user pipx && python3 -m pipx ensurepath

pipx install wsctl          # 安装并暴露 wsctl 到 PATH
```

### 方式二：uv（推荐，自带 Python 管理）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec "$SHELL" -l

uv python install 3.13      # 若系统 Python 太旧
uv tool install wsctl
```

### 方式三：venv + pip（完全自控）

```bash
python3.11 -m venv /opt/wsctl/venv
/opt/wsctl/venv/bin/pip install -U pip
/opt/wsctl/venv/bin/pip install wsctl
# 之后用绝对路径调用：/opt/wsctl/venv/bin/wsctl ...
```

> 不建议 `sudo pip install`。若必须，请加 `--user` 并处理 PATH。

### 方式四：Docker

仓库内提供 [`contrib/docker/Dockerfile`](contrib/docker/Dockerfile) 与
[`contrib/docker/docker-compose.yml`](contrib/docker/docker-compose.yml)。

> **必须在仓库根目录构建**（`-f contrib/docker/Dockerfile .`）：构建上下文是仓库根，
> 这样装进去的是**本地源码**；在 `contrib/docker` 目录里构建只会装到 PyPI 上的旧版。

```bash
# 方式一：docker build
docker build -t wsctl -f contrib/docker/Dockerfile .
docker run -d --name wsctl --restart unless-stopped \
  -p 127.0.0.1:7681:7681 -v "$PWD/data:/data" \
  -e WSCTL_ADMIN_PASSWORD='改成你的强密码' wsctl

# 方式二：docker compose（密码必须显式给出，否则直接报错退出）
cd contrib/docker
WSCTL_ADMIN_PASSWORD='改成你的强密码' docker compose up -d
```

镜像自带 `tmux`、`openssh-client`、`lrzsz`，因此 tmux 后端、SSH 后端与 ZMODEM
传输开箱可用。不给 `WSCTL_ADMIN_PASSWORD` 时，首次启动会生成随机密码并打印在日志里。

### 方式五：从源码

```bash
git clone https://github.com/ThzxxArt/wsctl.git
cd wsctl
python3.11 -m venv .venv && . .venv/bin/activate
pip install ".[dev]"        # 含开发/测试工具；仅使用可改为 pip install .
```

### 验证安装

```bash
wsctl --version             # 例如 wsctl 0.1.6
wsctl doctor                # 环境/配置/运行状态自检（--json 便于脚本消费）
```

`doctor` 会检查 Python 版本、数据目录可写、数据库、配置文件、默认 shell、
`tmux`/`ssh`/`lrzsz`、`SO_REUSEPORT`、监听端口占用、后台启动能力、**磁盘剩余空间**、
**密码策略**、**日志文件与轮转阈值**以及**系统时间**（TOTP 依赖时钟准确）。

「服务器」一项**优先探测你正在跑的那个实例**，而不是 `wsctl login` 留下的缓存地址——
后者指向早已下线的端口时，会把一台完全健康的机器报成「服务器失败」。显式传 `--url`
则按你说的查；没有实例在跑时才回退到缓存地址，并注明来源与 `wsctl logout` 清理方式。

> 想让 `wsctl <Tab>` 自动补全子命令与选项？见 [shell 补全](#shell-补全)。

### 可选依赖（extras）

| extra | 内容 | 用途 |
|---|---|---|
| `wsctl[win]` | `pywinpty` | Windows 下的 PTY 支持 |
| `wsctl[e2e]` | `playwright` | 浏览器级端到端测试 |
| `wsctl[dev]` | ruff / mypy / pytest 等 | 开发与测试 |

## 升级

先看当前版本，再升级，随后重启服务。

```bash
wsctl --version
```

**按安装方式选择**：

```bash
pipx upgrade wsctl                       # pipx
uv tool upgrade wsctl                    # uv
pip install -U wsctl                     # venv + pip
```

Docker：

```bash
docker pull wsctl            # 或重新 build
docker rm -f wsctl && docker run -d ...  # 用新镜像重建容器（数据在 volume 中不受影响）
```

源码方式：

```bash
cd wsctl && git pull && pip install -U ".[dev]"
```

**升级后重启服务**（二选一）：

```bash
wsctl restart                # 后台生命周期
sudo systemctl restart wsctl # systemd
```

**回退到旧版本**：

```bash
pipx install --force "wsctl==0.1.2"
# 或 pip install "wsctl==0.1.2"
```

> 说明：升级是向前兼容的，数据库在启动时自动做幂等迁移（`PRAGMA table_info` +
> `ALTER TABLE`）。若从小版本回退到大版本**之前**，请先备份（`wsctl backup`），
> 因为新版本可能已写入旧版本不认识的列（例如 0.1.4 为分享持久化新增的
> `share_*` 三列）。
>
> **零停机升级**：配合 `reuse_port = true` 与 tmux 后端，可先起新实例再停旧实例，
> 连会话都不中断。见 [零停机重启](#零停机重启)。

## 卸载

### 1. 先停止服务

```bash
wsctl stop                   # 后台生命周期启动的
sudo systemctl stop wsctl    # systemd 管理的
sudo systemctl disable wsctl
```

### 2. 卸载程序

```bash
pipx uninstall wsctl         # pipx
uv tool uninstall wsctl      # uv
pip uninstall wsctl          # venv + pip（在对应 venv 中执行）
```

Docker：

```bash
docker rm -f wsctl
docker rmi wsctl
```

systemd：删除单元文件并重载。

```bash
sudo rm -f /etc/systemd/system/wsctl.service
sudo systemctl daemon-reload
```

### 3. 清理数据与配置（可选）

卸载程序**不会**删除你的数据与配置（数据库、录制、凭据）。确认不再需要后手动删除：

```bash
rm -rf ~/.local/share/wsctl          # 数据目录：wsctl.db + recordings + run/（pid、日志）
rm -rf ~/.config/wsctl               # 配置文件 + CLI 凭据 credentials.json
# 若自定义过 data_dir / file_root，请按配置删除对应目录
```

> 想保留历史？先备份：`wsctl backup ~/wsctl-backup.tar.gz --include-config`。

### 4. 彻底清理清单

- [ ] 停止并禁用服务（后台实例 / systemd）
- [ ] 卸载程序（pipx / uv / pip / Docker / 源码 venv）
- [ ] 删除数据目录与配置目录
- [ ] 若用过反向代理，删除对应 `nginx` 站点并 `reload`
- [ ] 若用过自定义用户，删除专用系统账号（如 `userdel wsctl`）

## 快速开始

### 前台启动（首次试用）

```bash
wsctl serve
```

首次运行会在 `~/.local/share/wsctl/` 建库并创建 `admin`，**随机密码打印在标准错误**；
也可自行指定：

```bash
wsctl serve --admin-password '你的强密码'
```

浏览器打开 <http://127.0.0.1:7681>，用 `admin` 登录即得到一个 shell。

### 后台启动（常驻，无需 systemd）

```bash
wsctl start --admin-password '你的强密码'   # 后台运行
wsctl status                                # 查看状态（--json 便于脚本）
wsctl logs -f                               # 跟踪日志（首次密码也在这里）
wsctl stop                                  # 停止
```

### 用命令行连上去

```bash
wsctl login http://127.0.0.1:7681     # 交互式输入密码，凭据缓存到本地
wsctl connect                          # 在当前终端打开远程 shell（断线自动重连）
wsctl logout                           # 清除本地缓存凭据
```

启用了两步验证的账号同样可以从 CLI 登录：

```bash
wsctl login http://host:7681 -u admin --totp 123456   # 直接传验证码
wsctl login http://host:7681 -u admin                 # 省略 --totp，被要求时交互输入
WSCTL_TOTP=123456 wsctl login http://host:7681        # 脚本里用环境变量
```

## 使用教程

### 1. 会话管理（Web 端）

- 顶部 **`+`**：新建会话对话框——可填名称、命令、工作目录、选择后端
  （local / tmux / ssh）；选 ssh 时展开结构化表单（主机 / 用户 / 端口 / 私钥 /
  `ssh -o` 选项）。全部留空即创建默认 shell；`Alt+N` 则跳过对话框直接新建。
- 点击标签：切换；**双击标签**：重命名。
- 标签上的 **`×`**：**断开连接**（会话仍在服务器上运行，不会被杀掉）。
- **右键标签**：菜单选择「关闭标签（保持会话）」或「终止会话」。
- 顶部 **`会话`**：会话列表，可重新打开已断开的会话，或终止任意会话；支持
  **按名称筛选**与**全部断开**。
- 顶部 **`搜索`**（或 `Ctrl+Shift+F`）：在回滚缓冲中查找，`Enter` 跳到下一个。
- 断线后前端自动重连并回放屏幕，无需重新登录。

> 默认快捷键：`Alt+N` 新建、`Alt+W` 断开标签、`Alt+Shift+W` 终止会话、
> `Alt+→/←` 切换标签、`Alt+L` 会话列表、`Alt+F` 文件、`Alt+S` 设置、`Alt+H` 分享。

### 2. 从命令行使用

```bash
wsctl login http://host:7681 -u admin            # 缓存凭据
wsctl connect http://host:7681                   # 连接（新会话）
wsctl connect -s <session-id>                    # 连接已有会话
wsctl connect --no-reconnect                     # 关闭断线自动重连

wsctl session list
wsctl session new --name build --command "bash -l"
wsctl session rename <session-id> 构建
wsctl session attach <session-id>
wsctl session kill <session-id>
```

### 3. 会话后端：local / tmux / ssh

- **local（默认）**：shell 是服务的直接子进程，服务退出即结束。
- **tmux（跨重启恢复）**：shell 跑在 tmux 会话里，服务重启后自动重新挂载并重绘屏幕。

```bash
wsctl serve --backend tmux
wsctl session new --backend tmux --command "htop"
```

需要主机安装 `tmux`。优雅关闭会保留 tmux 会话（`tmux_preserve_on_shutdown`），
显式 kill 或空闲回收才会真正销毁。

- **ssh（跳板）**：会话直接是到远程主机的 ssh 连接（参数结构化传入，无 shell 注入）。

```bash
wsctl session new --ssh user@example.com
wsctl session new --ssh example.com --ssh-user root --ssh-port 2222 \
  --ssh-identity ~/.ssh/id_ed25519 --ssh-option StrictHostKeyChecking=accept-new
```

### 4. 文件面板

点击顶部 **文件** 打开右侧面板：在 `file_root`（默认当前用户家目录）内浏览目录、
点击下载、上传（按钮或拖拽）。

> 提醒：能开 shell 的账号本就能访问文件系统，文件面板不额外扩大权限面。

上传遇到同名文件会**拒绝并弹出确认**（HTTP 409），确认后才覆盖；不会静默替换。
目录条目超过 2000 项时列表会截断并明确提示。

### 5. 分享会话

点击顶部 **分享**：

- 默认生成**只读**链接（含二维码），任何人打开即可观看但**不能输入**。
- 勾选「允许输入（可写）」生成**可写**链接。
- 可选**有效期**（永久 / 10 分钟 / 1 小时 / 1 天），随时**撤销分享**。
- 再次打开会**复用**现有链接；修改选项后点「生成新链接」才会替换（旧链接失效）。
- **分享链接跨服务重启依然有效**（token 与会话一同持久化，重启后原样恢复、不轮换）；
  重命名会话也不会让已分发的链接失效。

分享链接形如 `http://host:7681/?session=<id>&share=<token>`，观看者无需账号。

### 6. 录制与回放

```bash
wsctl session record <session-id>                    # 开始录制（asciinema cast）
wsctl session record <session-id> --input            # 同时记录输入
wsctl session record-stop <session-id>               # 停止
wsctl session recording <session-id> -o out.cast     # 下载
```

Web 端点击 **录制** 开始/停止，**回放** 在浏览器内用 asciinema 播放器播放。
设 `auto_record = true` 可让每个会话自动录制。

### 7. 终端能力：Sixel 与 ZMODEM

- **Sixel**：内嵌 `@xterm/addon-image`，远端输出 Sixel 图像即可显示（`img2sixel`、`lsix`）。
- **ZMODEM**：点击 **传输** 启用后，远端 `sz 文件` 会在浏览器下载，`rz` 弹出上传选择框。
  默认关闭以免干扰常规 I/O。

### 8. 主题、字体与快捷键

点击顶部 **设置**：

- **主题**：内置 10 套终端主题（dracula、solarized、nord、gruvbox、monokai、
  one-dark、tokyo-night 等），也可粘贴 JSON 自定义。
- **字体**：字号 12–18，字体族可选系统等宽 / JetBrains Mono / Fira Code 等。
- **快捷键**：可编辑，冲突会提示；偏好保存在浏览器本地。

### 9. 用户、权限与二次验证

```bash
wsctl user add alice                       # 交互式设置密码
wsctl user list
wsctl user role alice admin                # 提升为管理员
wsctl user passwd alice                    # 改密（同时吊销该用户已有登录态）
wsctl user disable alice                   # 禁用（登录态立即失效）
wsctl user enable alice
wsctl user totp alice                      # 启用 TOTP 双因子（扫码确认）
wsctl user totp alice --disable
wsctl user del alice
```

- `admin`：可查看/管理所有会话与用户。
- `user`：仅能操作自己的会话。
- CLI 与 Web 都**不会允许移除最后一个管理员**，也不会让你禁用/删除自己。

管理员也可在 Web 端顶部 **管理** 完成同样操作（新建/改角色/禁用/重置密码/启用 2FA
并显示二维码），并查看审计、管理录制。

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

指标包含：运行状态、会话/客户端数、缓冲字节、会话创建数、WebSocket 连接数、上传数、
登录结果分类、**审计丢弃数**、**审计写入失败数**、**维护循环滞后**。开启
`log_json = true` 输出结构化 JSON 日志；用 `metrics_require_auth = true` 可要求登录
后才能抓取 `/metrics`。

### 12. Web 管理面（管理员）

点击顶部 **管理**（仅管理员可见），三个页签：

- **用户**：新建、切换角色、禁用/启用、重置密码（掩码输入）、启用两步验证（二维码）、删除。
- **审计**：按事件类型 / 用户 ID / IP 筛选，分页「加载更多」。
- **录制**：列出所有录制，可**回放**、下载、删除。
- **配置**：只读查看当前生效配置，每项标注「热更新」或「需重启」，可就地**重载**并
  显示具体变更项与解析错误。

## 后台运行（非 systemd）

不想用 systemd、也不想占用终端时，用内置生命周期：

```bash
wsctl start                       # 后台启动（子进程脱离终端）
wsctl status                      # 查看状态（--json 便于脚本消费）
wsctl logs -f                     # 跟踪日志（首次启动的 admin 密码在其中）
wsctl restart                     # 重启
wsctl reload                      # 让运行中的实例重载配置（SIGHUP）
wsctl stop                        # 优雅停止（超时后用 --force 强制结束）
```

- pid 文件：`<data_dir>/run/wsctl-<port>.pid`；日志：`<data_dir>/run/wsctl-<port>.log`
  （服务自身按大小轮转，保留 `log_backup_count` 份历史；`wsctl logs` 会**跨轮转文件**
  取末尾若干行，所以刚切过日志也不会「丢历史」）。
- `stop/status` 在发信号前会核对**进程身份**，不会误杀复用了同一 PID 的其他进程；
  崩溃留下的陈旧或损坏的 pid 文件会被自动清理。
- 已在运行时 `start` 会被拒绝：请用 `restart`、`start --force`（先停再启）或先 `stop`。
- `start` 与 `serve` 接受同样的参数（`--port`、`--config`、`--backend` 等）；
- **生命周期命令自动跟随正在运行的实例**：`stop`/`status`/`logs`/`reload`/`restart`/
  `doctor` 会去找这个数据目录里真正跑着的那个，而不是盯着默认地址报「未在运行」。
  传参只会**收窄**搜索、绝不放行：`--port` 只认该端口，`--host` 只认绑定在该地址的实例，
  两者都给则取交集；候选多于一个时列出并让你选，一个都不匹配就明说「未在运行」。
- `--daemon` 与 `--reuse-port` 互斥：多实例热切换请用 `--foreground` 或 systemd。
- 后台启动仅支持 Linux/macOS；Windows 请用 NSSM/计划任务运行 `wsctl serve --foreground`。

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
session_sliding_ttl = false   # true 时活动中的登录态自动续期
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
# log_file = "/var/log/wsctl/wsctl.log"   # 启用按大小轮转的日志文件
# log_max_bytes = 10485760               # 超过则轮转（copytruncate，保住追加型 fd）
# log_backup_count = 3                   # 保留几份历史

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

每个键都有对应环境变量，例如 `WSCTL_PORT=9000`、`WSCTL_DATA_DIR=/data`、
`WSCTL_ALLOWED_IPS='["10.0.0.0/8"]'`。

### 修改与热更新

```bash
wsctl config show                       # 查看生效配置（--json 便于脚本）
wsctl config get port                   # 打印单个配置项（未知项会给出最接近的候选）
wsctl config path                       # 配置文件路径
wsctl config edit                       # 用 $EDITOR 编辑（不存在则生成模板）
wsctl config set port 9000              # 写入配置（校验键名、值类型与整个文件；保留行内注释）
wsctl config validate                   # 校验配置文件
wsctl config reload                     # 让运行中的服务重载配置（HTTP）
wsctl reload                            # 同上，走 SIGHUP（无需已登录）
```

`config set` 会保留被改那一行尾部的注释（`port = 7681  # 监听端口` 改完注释还在），
也不会把注释掉的 `# port = 1234` 当成真配置项。

**配置写错时不会静默忽略**——错误会原样打印出来：

```console
$ wsctl reload
配置重载存在问题：
  - 配置项无效，已保持原值：max_sessions
    Input should be a valid integer, got 'not-an-int'
已重载 配置；变更项：无
$ echo $?
1
```

服务也会监听配置文件修改时间自动重载。可热更新的项包括白名单、限速、会话上限、
录制、文件根目录、指标开关等；**网络、TLS、数据目录、日志级别/轮转、Webhook 需重启**。
Web 管理面的「配置」页签会把每一项标成「热更新」或「需重启」。

## CLI 命令参考

```
wsctl serve                       启动服务（前台；--daemon 转后台）
wsctl start|stop|restart|status|logs|reload
                                  后台生命周期（非 systemd；status 支持 --json）
wsctl doctor                      环境/配置/运行状态自检（--json；--host/--port 选定实例）
wsctl backup FILE.tar.gz          一致性备份数据库与录制（--include-config）
wsctl restore FILE.tar.gz         从备份恢复（--force 覆盖，原库存为 .bak）
wsctl --version                   显示版本
wsctl connect [URL] [-s ID]       把本地终端连接到服务（断线自动重连；--no-reconnect）
wsctl login URL                   登录并缓存凭据（--totp CODE 支持两步验证）
wsctl logout                      清除本地缓存凭据
wsctl session list                列出会话（--json；显示用户名/后端/存活）
wsctl session new                 新建会话（--name/--command/--cwd/--backend/--ssh…；--json）
wsctl session rename ID NAME      重命名会话（--json）
wsctl session attach ID           连接到已有会话
wsctl session kill ID             终止会话
wsctl session record ID           开始录制（--input）
wsctl session record-stop ID      停止录制
wsctl session recording ID        下载录制（-o FILE）
wsctl user add|list|del|passwd|role|disable|enable|totp   （add/list 支持 --json）
wsctl audit                       查看审计日志（管理员；--event/--user-id/--ip/--json）
wsctl config show|get|path|edit|set|validate|reload      （show/get 支持 --json）
wsctl completion install|show     安装/查看 shell 补全（bash/zsh/fish/powershell）
wsctl version
```

`session` / `user` / `audit` / `config show` 都支持 `--json`，便于脚本消费：

```console
$ wsctl session list --json | jq -r '.[] | "\(.name)\t\(.owner)\t\(.backend)"'
```

> `wsctl config set` 会校验配置项名称与**值的类型**，并在写入前校验整个文件；非法值
> 会被拒绝且不落盘。未知项会报错并给出最接近的候选。
>
> 互斥或互相依赖的参数（如 `--daemon` 与 `--reuse-port`、`--ssl-cert` 与
> `--ssl-key`、`session new --ssh` 与 `--backend`）会以退出码 2 明确报错。

### shell 补全

`wsctl` 基于 Typer/Click，支持命令与选项的 Tab 补全。**bash** 下一键安装：

```bash
wsctl --install-completion        # 写入 ~/.bash_completions/wsctl.sh，并在 ~/.bashrc 追加 source
exec "$SHELL" -l                  # 或重开终端使其生效
```

查看补全脚本（可自行保存到任意位置 source）：

```bash
wsctl --show-completion
```

`--install-completion` 只覆盖 bash。**其余 shell 用 `wsctl completion`**，并按 `$SHELL`
自动识别（也可显式指定）：

```bash
wsctl completion install            # 自动识别 $SHELL 并安装
wsctl completion install zsh        # 显式指定：bash / zsh / fish / powershell
wsctl completion install fish
wsctl completion show zsh           # 只打印脚本，自行保存后 source
```

安装位置与生效方式：

| shell | 脚本位置 | 生效 |
|---|---|---|
| bash | `~/.bash_completions/wsctl.sh`（并写入 `~/.bashrc` 的 `source`） | 重开终端 |
| zsh | `~/.zfunc/_wsctl`（并写入 `fpath`/`compinit` 到 `~/.zshrc`） | 重开终端 |
| fish | `~/.config/fish/completions/wsctl.fish` | 重开终端 |
| PowerShell | 用户 profile 中的补全块 | 重开终端 |

生效后输入 `wsctl <Tab>` 即可补全子命令（`session`、`user`、`config`…）与选项：

```console
$ wsctl ser<Tab>        →  serve
$ wsctl session <Tab>   →  list  new  rename  kill  attach  record  record-stop  recording
```

## 部署

### systemd

仓库内提供 [`contrib/systemd/wsctl.service`](contrib/systemd/wsctl.service)。把
`ExecStart` 指向你的 wsctl（例如自建 venv 的绝对路径），令其仅监听本机、由反向代理
终止 TLS：

```ini
[Unit]
Description=wsctl 网页终端服务
After=network-online.target

[Service]
User=wsctl
Group=wsctl
WorkingDirectory=/var/lib/wsctl
ExecStart=/opt/wsctl/venv/bin/wsctl serve --host 127.0.0.1 --port 7681
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=15
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=false

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now wsctl
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
    proxy_read_timeout 3600s;
    proxy_buffering off;
}
```

位于代理后请设 `trust_proxy = true`（让限速与 IP 白名单看到真实客户端 IP）；
生产环境请用 HTTPS/WSS 并设 `cookie_secure = true`。

### 零停机重启

`reuse_port = true`（或 `--reuse-port`）让监听 socket 带 `SO_REUSEPORT`，新实例可在
旧实例退出前绑定同一端口，升级无「连接被拒」窗口：

```bash
wsctl serve --reuse-port &      # 运行中的实例
# 部署新版本后：
wsctl serve --reuse-port &      # 新实例接管新连接
kill <旧进程 PID>               # 旧实例排空后退出
```

两个实例短暂共享同一 `data_dir` 是安全的：每个实例通过 `instances` 租约表与
`term_sessions.instance_id` 只管理自己的会话，绝不会把对方的活跃会话标记为停止；
tmux 会话也按 `data_dir` 命名空间隔离。配合 **tmux 后端**，连会话本身也能跨重启存活。

### 备份与恢复

```bash
wsctl backup ~/wsctl-$(date +%F).tar.gz --include-config   # 一致性快照（含 WAL 已提交数据）
wsctl restore ~/wsctl-2026-01-01.tar.gz --force            # 覆盖前原库保存为 .bak
```

## 安全

Web 终端本质上是**远程代码执行服务**，请像对待 SSH 一样对待它：

- 默认开启认证，密码使用 Argon2 哈希；会话 token 仅存哈希。
- 密码策略：新建/改密强制非空且至少 8 位（API 与 CLI 同源强制）。
- 可选 TOTP 双因子（`wsctl user totp <用户>`）。
- 登录失败限速；`allowed_ips` 限制来源网段。
- WebSocket 握手校验 Origin 白名单（防 CSWSH）。
- 分享链接不可猜测、可设有效期、可撤销；只读链接拒绝输入；链接跨服务重启仍有效
  （token 与会话一同持久化）。
- 文件面板防目录穿越（含符号链接），上传有大小限制。
- 每会话连接数/内存/输入速率上限，防止资源耗尽。
- 全量审计日志；CLI 与 API 同源的用户管理不变量（不会锁死最后一个管理员）。
- 请务必使用 HTTPS/WSS，并以最小权限用户运行。

漏洞报告方式见 [SECURITY.md](https://github.com/ThzxxArt/wsctl/blob/main/SECURITY.md)。

## 常见问题与故障排查

**Q：首次运行没看到密码？**
密码打印在标准错误；后台运行时在日志里（`wsctl logs`）。若已丢失，用
`wsctl user passwd admin` 重设，或删除数据库后重启重新初始化。

**Q：开了两步验证后 CLI 全都登录不上？**
0.1.4 起 CLI 支持 2FA：`wsctl login <url> --totp 123456`，或省略 `--totp` 在被要求时
交互输入，脚本里可用 `WSCTL_TOTP` 环境变量。（0.1.3 及更早版本确实无法从 CLI 登录。）

**Q：改了配置 `wsctl reload` 说「变更项：无」？**
如果配置里有非法值，它会**明确报错**而不是假装没变化。看输出里的「配置重载存在问题」
一栏，或服务端日志。合法但没生效的改动，请确认该项是「热更新」而不是「需重启」
（Web 管理面的「配置」页签会逐项标注）。

**Q：上传文件报「文件已存在」？**
这是保护行为：同名文件默认拒绝（HTTP 409），在 Web 端弹窗确认后才会覆盖。

**Q：浏览器连不上 / 一直重连？**
检查反向代理是否转发 WebSocket（`Upgrade` / `Connection` 头）；若被判定未授权
（HTTP 401 / 关闭码 4401），重新登录；TOTP 场景确认系统时间正确。

**Q：关闭标签后 shell 会结束吗？**
不会。关闭标签默认只是**断开连接**，会话继续在服务器上运行。用标签右键菜单、
顶部「会话」列表，或 `Alt+Shift+W` 才能真正终止。

**Q：审计日志 / 数据库 / 录制文件会不会无限增长？**
有保留策略：`audit_retention_days`（默认 30）清理审计日志，
`term_session_retention_days`（默认 30）清理已结束的会话记录，
`recordings_retention_days` 与 `recordings_max_bytes` 约束录制文件（默认关闭，
0 表示不清理）。

**Q：`wsctl connect` 报「缺少服务器地址」？**
先 `wsctl login <url>`，或显式传 URL；token 也可用 `WSCTL_TOKEN` 提供。

**Q：tmux 会话没有跨重启恢复？**
确认主机安装了 `tmux`，且 `default_backend = "tmux"`（或创建时 `--backend tmux`）。
无头环境建议保持 `TERM` 可用（wsctl 会为 tmux 会话默认设为 `xterm-256color`）。

**Q：`rz`/`sz` 没反应？**
需先在 Web 端点击 **传输** 启用；远端需装 `lrzsz`。

**Q：如何只在本机使用、不要登录？**
`wsctl serve --no-auth`（**不安全**，切勿暴露到网络）。

**Q：不用 systemd，怎么让它后台常驻？**
`wsctl start` 启动、`wsctl status` 查看、`wsctl logs -f` 看日志、`wsctl stop` 停止。
详见[后台运行（非 systemd）](#后台运行非-systemd)。

**Q：`wsctl start` 说「已在运行」？**
同端口已有托管实例。用 `wsctl restart`、`start --force`（先停再启）或先 `wsctl stop`。
崩溃残留的陈旧 pid 文件会被 `status` 自动识别并清理（会核对进程身份，不会误杀）。

**Q：备份会不会漏掉刚写入的数据？恢复会覆盖现有数据吗？**
不会漏：`wsctl backup` 使用 SQLite 在线备份 API 生成一致快照（含 WAL 中已提交的
数据）。恢复时若数据库已存在，必须显式 `--force`，且原库会先保存为 `.bak`。

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

后端端到端场景（server / CLI / connect / tmux / crash / multiplex / daemon / 零停机重启）：

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
