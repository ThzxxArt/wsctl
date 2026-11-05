# wsctl 0.1.x → 0.2.0 迁移指南

> 0.2.0 是破坏性版本：Python 基线、WebSocket/REST 协议、配置格式、数据库 schema
> 与前端资产都变了。升级前先备份，回退只能 restore。

## 1. 升级前检查

```bash
wsctl --version                 # 应为 0.1.30
wsctl backup ~/wsctl-before-0.2.0.tar.gz --include-config
wsctl doctor
```

确认备份文件存在且非空。Docker 用户还要备份挂载的 `/data` volume。

## 2. 安装 0.2.0

```bash
pipx upgrade wsctl
# 或
uv tool upgrade wsctl
# 或
pip install -U wsctl
```

0.2.0 要求 **Python 3.12+**。3.11 环境请先升级解释器。

## 3. 迁移配置

```bash
wsctl config migrate
wsctl config validate
```

- 扁平 TOML 会转换为分节 TOML；
- 行内注释与值保留；
- 原文件备份为 `config.toml.v1.bak`；
- 已经是分节格式时不做任何修改。

新增可选项：

```toml
[terminal]
snapshot_mode = "auto"       # auto / always / off
shell_integration = true

[observability]
otel_enabled = false
otel_endpoint = "http://127.0.0.1:4318/v1/traces"

[push]
web_push_enabled = false
```

分节写法既可用平铺键名（`auth_required`），也可用分节短名
（`wsctl config set auth.required false`、`[files] root` / `max_upload`）；
两种写法落到同一模型字段，不会产生两份配置。

## 4. 迁移数据库

```bash
wsctl migrate --check
wsctl migrate --apply
```

- schema v5 → v6（head `0004_session_exit_code`）；
- 新增 `shares`、`api_keys`、`layouts`、`command_blocks`、`push_subscriptions`、
  `recordings`、`idempotency_keys`；
- `term_sessions` 新增 `generation` / `snapshot_mode` / `shell_integration` /
  `last_seq` / `exit_code`；
- `audit_logs` 新增 `request_id` / `prev_hash` / `hash`（哈希链从迁移点开始）；
  同时提供只读视图 `audit_events`（文档/工具可继续使用 Roadmap 中的名字）；
- 旧 `share_token` 自动导入 `shares`（只存哈希，旧链接继续可用）；
- 迁移前自动生成 `wsctl.db.pre-<版本>-<时间>.bak`，最多保留 3 份。

启动服务时也会自动升级；`--check` 只读，适合变更窗口前预演。

## 5. 重启

```bash
wsctl restart --rolling
# 或 systemd
sudo systemctl restart wsctl
```

不要在没有 `--rolling` 的情况下从 wsctl 自己的网页终端执行停止。

## 6. 协议与客户端

| 旧 | 新 | 说明 |
|---|---|---|
| `GET /api/...` | `GET /api/v2/...` | 旧路径保留一版，响应带 `Deprecation` 头 |
| `WS /ws` | `WS /ws/v2` | 旧端点仍说 v1；新客户端必须用 v2 |
| 分享 `?session=&share=` | `/s/<token>` | 旧 query 仍可用一版 |
| 单分享链接 | 多分享链接（read/write/control） | 旧 token 作为默认链接继续有效 |

CLI 的 `wsctl connect` 已切换到 v2，并会自动续传 `last_seq`。

## 7. 浏览器本地数据

- 布局：`wsctl-layout`（v1）会在首次加载时自动迁移为 `wsctl-layout-v2`；
- 偏好：`wsctl-prefs` 保留，新增字段使用默认值；
- 终端屏幕缓存：IndexedDB `wsctl-workspace`，按 `session + generation` 存储；
- 缓存无法解析时会自动丢弃并重新连接，不会阻塞登录。

## 8. 回退

0.2.0 的数据库不能被 0.1.x 直接读取。回退步骤：

```bash
wsctl stop
wsctl restore ~/wsctl-before-0.2.0.tar.gz --with-config --force
pipx install 'wsctl==0.1.30'
wsctl start
```

`restore` 会拒绝在服务运行时执行；这是有意的，避免半个进程持有旧库。

## 9. 升级后自检

```bash
wsctl doctor
wsctl migrate --check          # 应显示 0004_session_exit_code
curl -fsS http://127.0.0.1:7681/readyz
```

浏览器打开 `/`，确认：

1. 登录成功；
2. 新建会话可输入；
3. 断网/刷新后屏幕恢复（TUI 也应恢复）；
4. 分享链接可打开且权限正确；
5. 文件面板可列出目录；
6. 管理面板的审计链校验通过。
