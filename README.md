# wsctl

> A modern, Python-based web terminal server — a [ttyd](https://github.com/tsl0922/ttyd)
> superset with **persistent sessions**, **multi-user support** and **auditing**.

`wsctl` 单机部署的 Web 在线终端。一条命令起服务，浏览器或 CLI 得到完整 shell；
终端**会话独立于连接**，断线重连即恢复。

## Status

🚧 **v0.1.0 — 开发中**。API 与协议仍在演进，尚不可用于生产环境。

## Features (target)

- 🖥️ **多会话**：单服务托管 N 个终端，多标签切换
- 🔌 **会话与连接解耦**：断线重连回放屏幕，客户端断开不影响会话
- 👥 **多用户 + RBAC**：admin / user，会话归属与读写权限
- 📝 **审计日志**：结构化记录连接、输入输出与断开
- 🛡️ **安全内建**：argon2 · Origin 校验 · 登录限速 · IP allowlist · 可选 TOTP
- 📊 **可观测**：`/healthz` · Prometheus `/metrics` · 结构化 JSON 日志
- 📦 **零编译安装**：`pip install wsctl`

## 边界声明

`wsctl` 不在原始吞吐上对标 C + libuv 实现的 ttyd。稳定来自**架构**（会话与
连接解耦、输出背压、进程隔离），而非单连接速度。

## Install

```bash
pip install wsctl      # 尚未发布到 PyPI，当前请从源码安装
```

## Quick start

```bash
wsctl serve            # 启动服务
# 浏览器打开 http://127.0.0.1:7681
```

## Development

```bash
pip install -e ".[dev]"
ruff check . && mypy src && pytest
```

## License

[MIT](LICENSE) © 2026 ThzxxArt
