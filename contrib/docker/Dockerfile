# wsctl 容器镜像（0.2.0：前端预构建 + Python 运行时）
#
# 构建（必须在**仓库根目录**执行，这样装的是本地源码而不是 PyPI 上的旧版）：
#   docker build -t wsctl -f contrib/docker/Dockerfile .
#
# 运行：
#   docker run -d --name wsctl --restart unless-stopped \
#     -p 127.0.0.1:7681:7681 -v "$PWD/data:/data" \
#     -e WSCTL_ADMIN_PASSWORD='改成你的强密码' wsctl
#
# 首次启动若未提供 WSCTL_ADMIN_PASSWORD，服务会生成随机密码并打印在日志里。

# ---- 前端构建 ---------------------------------------------------------
FROM node:24-slim AS web

WORKDIR /web
RUN corepack enable

# 依赖层单独缓存：只有 lockfile 变化时才重新安装。
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY web ./
RUN WSCTL_WEB_OUTDIR=/web/dist pnpm build

# ---- 运行时 -----------------------------------------------------------
FROM python:3.12-slim

# tmux 提供跨重启恢复；openssh-client 供 SSH 后端；lrzsz 供 ZMODEM 传输
RUN apt-get update \
    && apt-get install -y --no-install-recommends tmux openssh-client lrzsz \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 先只拷贝打包元数据，依赖层可复用缓存；再拷贝源码装本体。
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src
# 预构建前端覆盖源码树里的静态目录：用户拿到的镜像不需要 Node。
COPY --from=web /web/dist ./src/wsctl/static
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

WORKDIR /
RUN rm -rf /build

ENV WSCTL_DATA_DIR=/data \
    WSCTL_CONFIG=/data/config.toml \
    WSCTL_FILE_ROOT=/workspace \
    WSCTL_HOST=0.0.0.0 \
    WSCTL_PORT=7681

VOLUME ["/data", "/workspace"]
EXPOSE 7681

# 以非 root 运行。The file panel root must not be the data directory: the
# SQLite database, recordings and VAPID config all live there.
RUN useradd --create-home --uid 10001 wsctl \
    && mkdir -p /data /workspace \
    && chown wsctl:wsctl /data /workspace

# 入口脚本负责首启建号：密码来自 WSCTL_ADMIN_PASSWORD，缺失则生成随机密码。
COPY --chown=wsctl:wsctl contrib/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER wsctl
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
