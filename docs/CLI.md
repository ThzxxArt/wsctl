# wsctl 0.2.0 CLI 完整教程

> 本文覆盖 `wsctl` 0.2.0 的**全部顶层命令与子命令**，包括每个选项、常见组合、
> 环境变量、退出码和故障恢复流程。命令以实际 `--help` 为准；升级后如果两者不一致，
> 以 `wsctl <命令> --help` 的输出为准。

## 目录

- [1. 先读这一节](#1-先读这一节)
- [2. 全局规则](#2-全局规则)
- [3. 安装后的第一条命令](#3-安装后的第一条命令)
- [4. 版本与自检](#4-版本与自检)
- [5. 服务生命周期](#5-服务生命周期)
- [6. 登录、登出与远程接入](#6-登录登出与远程接入)
- [7. 会话管理](#7-会话管理)
- [8. 用户与 TOTP](#8-用户与-totp)
- [9. 配置管理](#9-配置管理)
- [10. 审计](#10-审计)
- [11. 迁移、备份与恢复](#11-迁移备份与恢复)
- [12. Shell 补全与命令块](#12-shell-补全与命令块)
- [13. Web Push](#13-web-push)
- [14. 组合工作流](#14-组合工作流)
- [15. 环境变量](#15-环境变量)
- [16. 退出码与常见错误](#16-退出码与常见错误)
- [17. 完整命令索引](#17-完整命令索引)

---

## 1. 先读这一节

`wsctl` 有三类命令，先分清它们，后面就不会把「管理服务」和「连接会话」混在一起：

| 类型 | 作用于 | 典型命令 |
| :--- | :--- | :--- |
| **服务端生命周期** | 本机后台进程、systemd、Docker | `serve`、`start`、`stop`、`restart`、`status`、`logs`、`reload` |
| **本地数据管理** | 本机数据库、配置、备份 | `doctor`、`config`、`migrate`、`backup`、`restore`、`user` |
| **远程 API 客户端** | 已经运行的 wsctl 实例 | `login`、`connect`、`session`、`audit` |

同一个 `wsctl` 二进制同时承担这三类职责。绝大多数「命令没反应」都不是服务坏了，
而是**你正在对错误的实例、错误的配置路径，或未登录的 CLI 执行命令**。

---

## 2. 全局规则

### 2.1 帮助与版本

```bash
wsctl --help                 # 顶层命令列表
wsctl <命令> --help          # 任意命令的完整选项
wsctl user --help            # 子命令列表
wsctl session new --help     # 子命令选项
wsctl --version              # 只打印版本，例如 wsctl 0.2.0
wsctl version                # 命令形式，输出相同
```

### 2.2 配置优先级

从高到低：

1. 命令行参数（例如 `--port 9000`）
2. 环境变量（`WSCTL_*`，分节写法如 `WSCTL_SERVER__PORT` 也支持）
3. 配置文件（默认 `~/.config/wsctl/config.toml`）
4. 模型默认值

查看实际生效值：

```bash
wsctl config show
wsctl config show --json
wsctl config get port
wsctl config get data_dir
wsctl config path
```

### 2.3 找对实例

`status`、`logs`、`stop`、`restart`、`reload` 不指定 `--host/--port` 时，会
**优先跟随正在运行的实例**；指定参数只会收窄搜索，不会扩大范围。

```bash
wsctl status                 # 自动找当前实例
wsctl status --port 7682     # 只看 7682
wsctl status --host 0.0.0.0 --port 7681
wsctl status --json
```

如果同时有多个实例，`status` 会提示用 `--port` 指定；`stop/restart` 同理。
不要用默认端口去猜一个跑在 7682 上的实例。

### 2.4 认证缓存

`login` 会把服务器地址和 token 写到：

```text
~/.config/wsctl/credentials.json
```

之后的 `session`、`audit`、`config reload` 以及不带 URL 的 `connect` 会复用它。
`logout` 会同时通知服务端注销并删除本地文件。

### 2.5 JSON 输出

支持 `--json` 的命令适合脚本消费，字段是稳定的机器可读结构：

```bash
wsctl doctor --json
wsctl status --json
wsctl user list --json
wsctl session list --json
wsctl session new --json
wsctl session rename <id> <name> --json
wsctl audit --json
wsctl config show --json
wsctl config get port --json
```

### 2.6 退出码

| 退出码 | 含义 |
| ---: | :--- |
| `0` | 成功；`stop` 时「本来就没运行」也算成功 |
| `1` | 操作失败、服务未响应、认证失败、迁移失败等 |
| `2` | 命令组合非法（例如同时给 `--daemon` 和 `--foreground`） |
| `3` | 拒绝自断后路：在目标实例自己的终端里执行 `stop/restart` |
| 其他 | 子进程退出码；`connect` 会按 `128+信号` 规范化 |

---

## 3. 安装后的第一条命令

```bash
# 1. 后台启动，首次创建 admin，并启用端口不拒连的滚动重启
WSCTL_ADMIN_PASSWORD='换成你的强密码' wsctl start --reuse-port

# 2. 看状态
wsctl status

# 3. 自检
wsctl doctor

# 4. 打开浏览器，或从 CLI 登录
wsctl login http://127.0.0.1:7681
wsctl session list
```

> 首次启动不传 `WSCTL_ADMIN_PASSWORD` 时会生成随机密码，用 `wsctl logs` 查看。
> 密码创建后不会再被环境变量覆盖；改密请用 `wsctl user passwd`。

---

## 4. 版本与自检

### 4.1 `wsctl version`

```bash
wsctl version
wsctl --version
```

### 4.2 `wsctl doctor`

检查 Python 版本、数据目录、数据库、配置文件、默认 shell、`tmux`/`ssh`/`lrzsz`、
`SO_REUSEPORT`、监听端口、后台启动能力、磁盘空间、密码策略、日志文件、系统时间
以及当前实例是否可达。

```bash
wsctl doctor
wsctl doctor --json
wsctl doctor --config ./config.toml
wsctl doctor --url https://term.example.com
wsctl doctor --host 127.0.0.1 --port 7682
```

| 选项 | 说明 |
| :--- | :--- |
| `--config`, `-c` | 指定配置文件 |
| `--url` | 显式检查某个服务器地址，而不是缓存地址 |
| `--host` | 只检查该监听地址上的实例 |
| `--port` | 只检查该端口上的实例 |
| `--json` | JSON 输出，失败时退出码仍为 `1` |

`doctor` 不创建数据库；它用只读连接检查已有数据库。存在 `[red]失败[/]` 行时，
命令会以退出码 `1` 结束，适合直接放进 CI 或启动前检查。

---

## 5. 服务生命周期

### 5.1 `wsctl serve`：前台启动

```bash
wsctl serve
wsctl serve --host 127.0.0.1 --port 7681
wsctl serve --config ./config.toml
wsctl serve --daemon                 # 等价于 wsctl start
wsctl serve --foreground             # 显式前台（默认）
```

| 选项 | 说明 |
| :--- | :--- |
| `--host` | 监听地址 |
| `--port` | 监听端口 |
| `--config`, `-c` | 配置文件路径 |
| `--no-auth` | 关闭认证，**不要暴露到网络** |
| `--admin-password` | 首次启动创建 admin 的密码；也读 `WSCTL_ADMIN_PASSWORD` |
| `--ssl-cert` / `--ssl-key` | 使用自己的 TLS 证书 |
| `--tls-self-signed` | 自动生成并复用自签证书，以 HTTPS 启动 |
| `--log-level` | 日志级别 |
| `--log-json` | JSON 日志 |
| `--new` | 启动时创建并运行指定命令的会话 |
| `--backend` | 默认后端：`local` 或 `tmux` |
| `--reuse-port` | 以 `SO_REUSEPORT` 绑定，为滚动重启做准备 |
| `--daemon` / `--foreground` | 后台 / 前台，互斥 |
| `--timeout` | 后台启动等待就绪的秒数，默认 20 秒 |

示例：

```bash
wsctl serve --host 127.0.0.1 --port 7681 --tls-self-signed
wsctl serve --backend tmux --new 'tail -f /var/log/syslog'
```

> `serve` 是前台进程，退出即服务结束。日常应该用 `start` 或 systemd。

### 5.2 `wsctl start`：后台启动

```bash
wsctl start
wsctl start --port 7682
wsctl start --host 0.0.0.0 --port 7681 --reuse-port
wsctl start --force                  # 已在运行则先停止再启动
wsctl start --backend tmux
wsctl start --new 'htop' --name monitor
```

| 选项 | 说明 |
| :--- | :--- |
| `--host` / `--port` | 监听地址与端口 |
| `--config`, `-c` | 配置文件 |
| `--no-auth` | 关闭认证（危险） |
| `--admin-password` | 首次启动的管理员密码；也读 `WSCTL_ADMIN_PASSWORD` |
| `--ssl-cert` / `--ssl-key` | 自定义 TLS |
| `--tls-self-signed` | 自签 HTTPS |
| `--log-level` / `--log-json` | 日志 |
| `--new` / `--backend` | 启动时创建会话 / 默认后端 |
| `--timeout` | 等待就绪秒数，默认 20 |
| `--reuse-port` | 允许后续 `restart --rolling` |
| `--force` | 已在运行时先停再起 |

`start` 会写 PID 与日志到：

```text
<data_dir>/run/wsctl-<port>.pid
<data_dir>/run/wsctl-<port>.log
```

默认 `data_dir` 是 `~/.local/share/wsctl`，可用 `wsctl config get data_dir` 确认。

### 5.3 `wsctl stop`：停止后台实例

```bash
wsctl stop
wsctl stop --port 7682
wsctl stop --timeout 30
wsctl stop --force                  # 优雅超时后 SIGKILL
wsctl stop --i-know-this-drops-my-connection
```

| 选项 | 说明 |
| :--- | :--- |
| `--host` / `--port` | 指定实例 |
| `--config`, `-c` | 配置文件 |
| `--timeout` | 优雅退出等待秒数，默认 15 |
| `--force` | 超时后强制结束 |
| `--i-know-this-drops-my-connection` | 确认接受当前连接被切断 |

**自断后路保护**：如果你正在该实例托管的终端里执行 `stop`，CLI 会拒绝，因为
停止服务会连带你这条命令的 shell 一起结束。确认要这样做时才加
`--i-know-this-drops-my-connection`。升级场景请优先用 `restart --rolling`。

`stop` 是幂等的：本来就没运行会打印「本就未在运行（无需操作）」并返回 `0`，
所以 `wsctl stop && wsctl start` 可以安全重复执行。

### 5.4 `wsctl restart`：重启

```bash
wsctl restart
wsctl restart --port 7682
wsctl restart --rolling            # 推荐：先起后停
wsctl restart --rolling --timeout 60
wsctl restart --force              # 非滚动模式下的强制停止
```

| 选项 | 说明 |
| :--- | :--- |
| `--host` / `--port` | 指定实例 |
| `--config`, `-c` | 配置文件 |
| `--no-auth` | 重启后关闭认证 |
| `--admin-password` | 首次启动密码 |
| `--ssl-cert` / `--ssl-key` / `--tls-self-signed` | TLS |
| `--log-level` / `--log-json` | 日志 |
| `--new` / `--backend` | 启动参数 |
| `--timeout` | 等待就绪秒数，默认 20 |
| `--rolling` | 新实例先接管，再停旧实例 |
| `--i-know-this-drops-my-connection` | 确认接受连接被切断 |

> **滚动重启的真实边界**：`--rolling` 依赖第一次启动时的 `--reuse-port`，保证
> **端口不会出现拒绝连接的空窗**。tmux 后端的会话可以继续存活；本地 PTY 和 SSH
> 会话仍会随旧进程结束，浏览器/CLI 需要重连。它不是“所有会话永生”。

### 5.5 `wsctl status`

```bash
wsctl status
wsctl status --json
wsctl status --port 7682
```

输出 PID、监听地址、版本、运行时长与会话数。JSON 适合监控脚本。

### 5.6 `wsctl logs`

```bash
wsctl logs
wsctl logs -f
wsctl logs -n 500
wsctl logs --port 7682 -f
```

| 选项 | 说明 |
| :--- | :--- |
| `--host` / `--port` | 指定实例 |
| `--follow`, `-f` | 持续跟踪 |
| `--lines`, `-n` | 末尾行数，默认 100 |
| `--config`, `-c` | 配置文件 |

### 5.7 `wsctl reload`

发送 `SIGHUP`，让运行中的进程重新读取配置文件；不会重启进程。

```bash
wsctl reload
wsctl reload --port 7682
```

只对“可热更新”的配置项生效；需要重启的项不会假装生效。Web 管理台或
`wsctl config reload` 会返回具体变更项与错误。

---

## 6. 登录、登出与远程接入

### 6.1 `wsctl login`

把服务器地址与 token 缓存到本地，供后续 API 命令使用。

```bash
wsctl login http://127.0.0.1:7681
wsctl login https://term.example.com -u admin
wsctl login http://127.0.0.1:7681 -u admin -p '密码'
wsctl login http://127.0.0.1:7681 -u admin --totp 123456
```

| 参数/选项 | 说明 |
| :--- | :--- |
| `url` | 必填，例如 `http://127.0.0.1:7681` |
| `--username`, `-u` | 用户名，默认 `admin` |
| `--password`, `-p` | 密码；省略则交互输入 |
| `--totp` | TOTP 验证码；也读 `WSCTL_TOTP` |

> `-p` 会出现在 shell history 中；共享机器建议交互输入，或使用受控的
> `WSCTL_*` 环境变量。

### 6.2 `wsctl logout`

```bash
wsctl logout
```

通知服务端注销当前 token，并删除 `~/.config/wsctl/credentials.json`。

### 6.3 `wsctl connect`

把**当前终端**接入远程会话；需要 POSIX 交互式终端。

```bash
wsctl connect
wsctl connect http://127.0.0.1:7681
wsctl connect https://term.example.com -s 8f3a2c1d
wsctl connect --token "$WSCTL_TOKEN"
wsctl connect --no-reconnect
```

| 参数/选项 | 说明 |
| :--- | :--- |
| `url` | 省略时使用 `login` 缓存的地址 |
| `--session`, `-s` | 连接已有会话；省略则创建新会话 |
| `--token` | Bearer token；也读 `WSCTL_TOKEN` |
| `--no-reconnect` | 断线后不自动重连 |

`connect` 会断线自动重连，并使用协议 v2 的续传/快照能力。它不同于
`wsctl session attach`：`connect` 可以在没有缓存 token 时显式传 URL/token。

---

## 7. 会话管理

所有 `session` 子命令都通过 HTTP API 操作**运行中的实例**；省略 `--url` 时使用
`login` 缓存的地址。

### 7.1 `wsctl session list`

```bash
wsctl session list
wsctl session list --json
wsctl session list --url http://127.0.0.1:7681
```

列出会话 id、名称、后端、属主、客户端数、状态等。

### 7.2 `wsctl session new`

```bash
# 本地 PTY
wsctl session new -n build --cwd /srv/app

# 运行指定命令而不是 shell
wsctl session new -n logs -x 'tail -f /var/log/syslog'

# tmux（跨重启恢复）
wsctl session new -n work --backend tmux

# SSH 跳板
wsctl session new -n prod \
  --ssh 10.0.0.10 --ssh-user deploy --ssh-port 22 \
  --ssh-identity ~/.ssh/id_ed25519 \
  --ssh-option StrictHostKeyChecking=accept-new
```

| 选项 | 说明 |
| :--- | :--- |
| `--name`, `-n` | 会话名称 |
| `--command`, `-x` | 替代 shell 的命令 |
| `--cwd` | 工作目录 |
| `--backend` | `local`、`tmux`、`ssh` |
| `--ssh` | SSH 目标主机，隐含 `--backend ssh` |
| `--ssh-user` | SSH 用户名 |
| `--ssh-port` | SSH 端口 |
| `--ssh-identity` | SSH 私钥 |
| `--ssh-option` | 额外 `ssh -o` 选项，可重复 |
| `--url` | 服务器地址 |
| `--json` | JSON 输出 |

> `--ssh-user/--ssh-port/--ssh-identity/--ssh-option` 必须配合 `--ssh`；
> `--ssh` 不能和 `--backend local/tmux` 同时使用。

### 7.3 `wsctl session rename`

```bash
wsctl session rename 8f3a2c1d "生产日志"
wsctl session rename 8f3a2c1d "生产日志" --json
```

只改显示名称，不影响已分发分享链接的有效性。

### 7.4 `wsctl session attach`

```bash
wsctl session attach 8f3a2c1d
wsctl session attach 8f3a2c1d --no-reconnect
wsctl session attach 8f3a2c1d --url http://127.0.0.1:7681
```

把当前终端绑定到已有会话，适合在服务器本机或跳板机上接管会话。

### 7.5 `wsctl session kill`

```bash
wsctl session kill 8f3a2c1d
```

终止会话并释放其 PTY/客户端。tmux 后端会同时结束对应 tmux 会话。

### 7.6 录制

```bash
wsctl session record 8f3a2c1d
wsctl session record 8f3a2c1d --input
wsctl session record-stop 8f3a2c1d
wsctl session recording 8f3a2c1d -o session.cast
```

| 命令 | 说明 |
| :--- | :--- |
| `session record` | 开始写入 asciinema cast；`--input` 同时记录键盘输入（可能含密码） |
| `session record-stop` | 停止录制 |
| `session recording` | 下载/输出 cast；`-o` 写文件，省略则写 stdout |

> 录制文件默认在数据目录的 `recordings/`，权限 `0600`；`--input` 会把输入
> 原样写入，敏感环境慎用。

---

## 8. 用户与 TOTP

用户命令直接操作本机数据库，不需要服务已启动；修改会同步影响运行中的实例
（HTTP 路径立即终止，CLI 路径由维护循环兜底）。

### 8.1 创建与查看

```bash
wsctl user add alice
wsctl user add alice -p '强密码至少8位'
wsctl user add alice -p '强密码至少8位' --role admin
wsctl user list
wsctl user list --json
```

### 8.2 改密、角色、禁用

```bash
wsctl user passwd alice
wsctl user passwd alice -p '新的强密码'
wsctl user role alice admin
wsctl user role alice user
wsctl user disable alice
wsctl user enable alice
```

- 改密会吊销该用户已有登录态。
- 禁用立即让登录态失效，并终止其会话。
- 不能删除/降级/禁用最后一个启用的管理员。
- API 路径不能禁用/删除自己；CLI 也遵守“最后管理员”保护。

### 8.3 删除

```bash
wsctl user del alice
```

删除用户会清理其登录态；运行中的会话由服务端终止。不可恢复，先确认备份。

### 8.4 TOTP

```bash
wsctl user totp alice            # 生成密钥/URI/二维码，输入一次验证码确认
wsctl user totp alice --disable  # 关闭
```

启用后，`wsctl login` 需要 `--totp` 或交互输入验证码；脚本可用 `WSCTL_TOTP`。

---

## 9. 配置管理

### 9.1 查看

```bash
wsctl config show
wsctl config show --json
wsctl config path
wsctl config get port
wsctl config get auth_required --json
```

`config get` 默认输出裸值，适合：

```bash
PORT=$(wsctl config get port)
```

### 9.2 校验、迁移、编辑

```bash
wsctl config validate
wsctl config migrate                 # 0.1.x 扁平 → 0.2.0 分节，备份 .v1.bak
wsctl config migrate --no-backup
wsctl config edit                    # 用 $EDITOR 打开，缺失则创建
```

`config migrate` 只移动键与注释，不改值；已经是分节格式时不做任何事。

### 9.3 设置值

```bash
wsctl config set server.port 9000
wsctl config set auth.required false
wsctl config set files.root '"/srv/workspace"'
wsctl config set terminal.scrollback_bytes '"8MiB"'
wsctl config set allowed_ips '["10.0.0.0/8"]'
wsctl config set log_json true
```

值按 TOML 语法解析：字符串要带引号，布尔写 `true/false`，数组写
`["a", "b"]`。写入前会做单项类型校验和整文件校验，失败回滚，并保留行内注释。

### 9.4 让改动生效

```bash
wsctl reload                 # SIGHUP，按配置项的热更新等级生效
wsctl config reload          # 走 API，需要管理员登录；会打印 changed/errors
```

配置页/命令会区分：

- **立即生效**（如 `allowed_ips`、`trust_proxy`）
- **仅新会话/新连接生效**（如默认 shell、滚动缓冲大小）
- **需要重启**（如监听端口、数据目录、数据库 URL）

不要假设所有字段都能热更新；`config get` / 管理台会标注。

---

## 10. 审计

```bash
wsctl audit
wsctl audit -n 200
wsctl audit --event login
wsctl audit --user-id 3
wsctl audit --ip 203.0.113.9
wsctl audit --json
wsctl audit --url http://127.0.0.1:7681
```

| 选项 | 说明 |
| :--- | :--- |
| `--limit`, `-n` | 条数，默认 50 |
| `--event` | 事件类型，例如 `login`、`file_edit`、`session_attach` |
| `--user-id` | 按用户 id |
| `--ip` | 按来源 IP |
| `--json` | JSON 输出 |

只有管理员可读；普通用户会收到 403。

---

## 11. 迁移、备份与恢复

### 11.1 数据库迁移

```bash
wsctl migrate                 # 不带 --apply 时只显示当前/最新版本
wsctl migrate --check
wsctl migrate --apply         # 执行迁移
```

执行前先备份：

```bash
wsctl backup ~/wsctl-before-migrate.tar.gz --include-config
wsctl migrate --apply
wsctl restart --rolling
```

### 11.2 一致性备份

```bash
wsctl backup ~/wsctl-backup.tar.gz
wsctl backup ~/wsctl-backup.tar.gz --include-config
```

`--include-config` 会把 `config.toml` 一起打包；归档包含数据库、录制、manifest。
数据库使用 SQLite 在线备份 API，服务运行中也可执行。

### 11.3 恢复

恢复前必须先停止服务：

```bash
wsctl stop
wsctl restore ~/wsctl-backup.tar.gz
wsctl start
```

| 选项 | 说明 |
| :--- | :--- |
| `--force` | 覆盖现有数据库；原库保存为 `.bak` |
| `--with-config` | 同时恢复配置 |
| `--config`, `-c` | 配置文件 |

恢复会拒绝在运行中的实例上执行，并检查归档路径、SQLite 完整性和 live lease。
恢复后建议：

```bash
wsctl doctor
wsctl status
```

### 11.4 迁移到新机器

```bash
# 旧机器
wsctl backup /tmp/wsctl-move.tar.gz --include-config
scp /tmp/wsctl-move.tar.gz new-host:/tmp/

# 新机器
pipx install wsctl
wsctl stop
wsctl restore /tmp/wsctl-move.tar.gz --with-config
wsctl doctor
wsctl start --reuse-port
```

> 配置里可能含 VAPID 私钥、Webhook URL、TLS 路径；移动后要按新机器路径修正，
> 并确认 `data_dir`、`file_root`、`recordings_dir` 的权限。

---

## 12. Shell 补全与命令块

### 12.1 补全

```bash
wsctl completion install              # 自动识别 shell
wsctl completion install bash
wsctl completion install zsh
wsctl completion install fish
wsctl completion install powershell
wsctl completion show bash             # 只打印，不修改 rc
```

`install` 会写入对应 shell 的补全文件/rc；`show` 适合手动放到任意位置后
`source`。顶层 `wsctl --install-completion` / `--show-completion` 是 Typer
自带入口，`wsctl completion` 补齐了 bash/zsh/fish/PowerShell。

### 12.2 Shell Integration（命令块）

```bash
wsctl shell-integration install
wsctl shell-integration install bash
wsctl shell-integration install zsh --write-rc
wsctl shell-integration install fish --write-rc
wsctl shell-integration install powershell --write-rc
wsctl shell-integration show bash
```

默认只写脚本到：

```text
~/.config/wsctl/shell-integration/wsctl.sh
（zsh 为 .sh，fish 为 .fish，PowerShell 为 .ps1）
```

`--write-rc` 才会把 `source` 行追加到 `~/.bashrc`、`~/.zshrc`、
`~/.config/fish/config.fish` 或 PowerShell profile。安装后重新打开 shell。
它提供 OSC 133/633 命令块（命令、cwd、耗时、退出码、重跑/复制）。

---

## 13. Web Push

```bash
wsctl push keys
```

输出一段可直接放入配置文件的 TOML：

```toml
[push]
web_push_enabled = true
web_push_vapid_public = "..."
web_push_vapid_private = "..."
web_push_subject = "mailto:admin@example.com"
```

写入配置后：

```bash
wsctl config validate
wsctl restart --rolling
```

Web Push 需要 HTTPS 或 localhost；浏览器端在“设置 → 通知”里授权。

---

## 14. 组合工作流

### 14.1 首次部署

```bash
pipx install wsctl
WSCTL_ADMIN_PASSWORD='强密码' wsctl start --reuse-port
wsctl status
wsctl doctor
wsctl login http://127.0.0.1:7681
wsctl session new -n first
```

### 14.2 升级 wsctl

```bash
wsctl backup ~/wsctl-pre-upgrade.tar.gz --include-config
pipx upgrade wsctl
wsctl migrate --check
wsctl restart --rolling
wsctl doctor
```

如果当前版本没有 `--reuse-port`，第一次升级需要一次普通重启；之后才具备滚动重启
条件。`--rolling` 不保证本地/SSH 会话存活。

### 14.3 日常排障

```bash
wsctl status --json
wsctl logs -n 200
wsctl doctor --json
wsctl session list
wsctl audit -n 100 --event login
```

### 14.4 管理员被锁在门外

```bash
# 本机直接改密（不需要服务已启动）
wsctl user passwd admin -p '新的强密码'

# 检查实例
wsctl status
wsctl doctor

# 必要时重启
wsctl restart --rolling
```

不要在 `--no-auth` 暴露网络；它只适合本机临时排障。

### 14.5 定期备份（cron）

```cron
0 3 * * * /home/me/.local/bin/wsctl backup /backup/wsctl-$(date +\%F).tar.gz --include-config
```

cron 环境没有你的 shell PATH，建议使用绝对路径，并在脚本里显式设置
`WSCTL_DATA_DIR` / `WSCTL_CONFIG`（如果你的路径不是默认值）。

### 14.6 只读排障会话

```bash
wsctl session new -n diag -x 'journalctl -f'
wsctl session record <id>
# 完成后
wsctl session record-stop <id>
wsctl session recording <id> -o diag.cast
```

### 14.7 用 pipe 消费 JSON

```bash
wsctl session list --json | jq -r '.[] | [.id, .name, .backend] | @tsv'
wsctl doctor --json | jq '.[] | select(.result | test("失败"))'
```

---

## 15. 环境变量

| 变量 | 作用 |
| :--- | :--- |
| `WSCTL_CONFIG` | 配置文件路径 |
| `WSCTL_DATA_DIR` | 数据目录（数据库、录制、run） |
| `WSCTL_HOST` / `WSCTL_PORT` | 默认监听地址/端口 |
| `WSCTL_SERVER__HOST` / `WSCTL_SERVER__PORT` | 分节写法，等价 |
| `WSCTL_ADMIN_PASSWORD` | 首次启动创建 admin 的密码 |
| `WSCTL_TOKEN` | `connect` / `session attach` 的 Bearer token |
| `WSCTL_TOTP` | 登录时的 TOTP 验证码 |
| `WSCTL_CA_CERT` | 连接自签 HTTPS 时的 CA 证书路径 |
| `WSCTL_LOG_LEVEL` / `WSCTL_LOG_JSON` | 日志级别 / JSON |
| `WSCTL_FILE_ROOT` | 文件面板根目录 |
| `WSCTL_TRUST_PROXY` / `WSCTL_TRUSTED_PROXIES` | 反向代理信任 |
| `WSCTL_COOKIE_SECURE` | Cookie 仅 HTTPS |

环境变量优先级高于配置文件、低于命令行参数。查看最终值用
`wsctl config show`。

---

## 16. 退出码与常见错误

| 现象 | 原因 | 处理 |
| :--- | :--- | :--- |
| `未在运行` | 默认端口没有实例 | `wsctl status --port <实际端口>`；用 `--port` 指定 |
| `已在运行` | 重复 `start` | 用 `restart`，或 `start --force` |
| `拒绝停止` | 在目标实例自己的终端里执行 `stop/restart` | 改用 `restart --rolling`，或显式加 `--i-know-this-drops-my-connection` |
| `HTTP 401` | `login` 过期/未登录 | `wsctl login ...`；TOTP 账号补 `--totp` |
| `HTTP 403` | 非管理员或权限不足 | 用管理员登录；检查用户角色 |
| `HTTP 404` 会话不存在 | 会话已结束或 id 错误 | `wsctl session list` |
| `HTTP 409` | 重名/冲突/幂等键冲突 | 换名称或稍后重试 |
| `迁移失败` | 数据库版本/损坏/权限 | 先 `wsctl backup`，看 `wsctl logs`，再 `migrate --check` |
| `doctor` 退出 1 | 有失败项 | 按 `doctor --json` 的失败行处理 |
| 连接自签 HTTPS 失败 | CLI 不信任证书 | 使用实例本地证书自动固定，或设置 `WSCTL_CA_CERT` |

> 不要在 wsctl 网页终端里执行裸 `wsctl stop` / `wsctl restart`。CLI 会拦截，
> 但理解原因比记住报错更重要：停止服务会结束承载这条命令的 shell。

---

## 17. 完整命令索引

```text
wsctl --help
wsctl --version
wsctl version
wsctl doctor [--config PATH] [--url URL] [--host HOST] [--port PORT] [--json]
wsctl backup OUTPUT [--config PATH] [--include-config]
wsctl restore ARCHIVE [--config PATH] [--force] [--with-config]
wsctl migrate [--config PATH] [--check] [--apply]
wsctl serve [--host HOST] [--port PORT] [--config PATH] [--no-auth]
             [--admin-password PASSWORD] [--ssl-cert PATH] [--ssl-key PATH]
             [--tls-self-signed] [--log-level LEVEL] [--log-json]
             [--new COMMAND] [--backend local|tmux] [--reuse-port]
             [--daemon|--foreground] [--timeout SECONDS]
wsctl start [--host HOST] [--port PORT] [--config PATH] [--no-auth]
             [--admin-password PASSWORD] [--ssl-cert PATH] [--ssl-key PATH]
             [--tls-self-signed] [--log-level LEVEL] [--log-json]
             [--new COMMAND] [--backend local|tmux] [--timeout SECONDS]
             [--force] [--reuse-port]
wsctl stop [--host HOST] [--port PORT] [--config PATH] [--timeout SECONDS]
            [--force] [--i-know-this-drops-my-connection]
wsctl restart [--host HOST] [--port PORT] [--config PATH] [--no-auth]
              [--admin-password PASSWORD] [--ssl-cert PATH] [--ssl-key PATH]
              [--tls-self-signed] [--log-level LEVEL] [--log-json]
              [--new COMMAND] [--backend local|tmux] [--timeout SECONDS]
              [--rolling] [--i-know-this-drops-my-connection]
wsctl status [--host HOST] [--port PORT] [--config PATH] [--json]
wsctl logs [--host HOST] [--port PORT] [--config PATH] [--follow|-f] [--lines|-n N]
wsctl reload [--host HOST] [--port PORT] [--config PATH]
wsctl audit [--limit|-n N] [--url URL] [--event EVENT] [--user-id ID] [--ip IP] [--json]
wsctl login URL [--username|-u USER] [--password|-p PASSWORD] [--totp CODE]
wsctl logout
wsctl connect [URL] [--session|-s SID] [--token TOKEN] [--no-reconnect]
wsctl user add USERNAME [--password|-p PASSWORD] [--role admin|user] [--config PATH] [--json]
wsctl user list [--config PATH] [--json]
wsctl user del USERNAME [--config PATH]
wsctl user passwd USERNAME [--config PATH] [--password|-p PASSWORD]
wsctl user role USERNAME ROLE [--config PATH]
wsctl user totp USERNAME [--disable] [--config PATH]
wsctl user disable USERNAME [--config PATH]
wsctl user enable USERNAME [--config PATH]
wsctl config show [--config PATH] [--json]
wsctl config path [--config PATH]
wsctl config get KEY [--config PATH] [--json]
wsctl config validate [--config PATH]
wsctl config migrate [--config PATH] [--no-backup]
wsctl config edit [--config PATH]
wsctl config set KEY VALUE [--config PATH]
wsctl config reload [--url URL]
wsctl session list [--url URL] [--json]
wsctl session kill SID [--url URL]
wsctl session new [--name|-n NAME] [--command|-x CMD] [--cwd DIR]
                  [--backend local|tmux|ssh] [--ssh HOST] [--ssh-user USER]
                  [--ssh-port PORT] [--ssh-identity PATH] [--ssh-option OPTION]...
                  [--url URL] [--json]
wsctl session rename SID NAME [--url URL] [--json]
wsctl session attach SID [--url URL] [--token TOKEN] [--no-reconnect]
wsctl session record SID [--input] [--url URL]
wsctl session record-stop SID [--url URL]
wsctl session recording SID [--output|-o PATH] [--url URL]
wsctl completion install [bash|zsh|fish|powershell]
wsctl completion show [bash|zsh|fish|powershell]
wsctl shell-integration show [bash|zsh|fish|powershell]
wsctl shell-integration install [bash|zsh|fish|powershell] [--write-rc]
wsctl push keys
```

---

## 相关文档

- [README](../README.md) — 安装、配置、部署与完整卸载
- [MIGRATION-0.2.0](MIGRATION-0.2.0.md) — 0.1.x → 0.2.0 备份、配置、数据库迁移
- [SECURITY](../SECURITY.md) — 威胁模型、凭据、分享与文件安全
- [FRONTEND](FRONTEND.md) — 前端开发、构建与浏览器测试
- [ROADMAP-0.2.0](ROADMAP-0.2.0.md) — 0.2.0 范围、实施结果与边界
