# wsctl 容器镜像
#
# 构建：  docker build -t wsctl contrib/docker
# 运行：  docker run --rm -p 127.0.0.1:7681:7681 -v "$PWD/data:/data" wsctl
#
# 首次启动会在日志里打印 admin 的随机密码；也可以先设置 WSCTL_ADMIN…
# （此处通过 --admin-password 传入，见 docker-compose.yml）。
FROM python:3.12-slim

# tmux 提供跨重启恢复；如需 SSH 跳板/rz-sz，可再装 openssh-client / lrzsz
RUN apt-get update \
    && apt-get install -y --no-install-recommends tmux \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir wsctl

ENV WSCTL_DATA_DIR=/data \
    WSCTL_FILE_ROOT=/data \
    WSCTL_HOST=0.0.0.0 \
    WSCTL_PORT=7681

VOLUME ["/data"]
EXPOSE 7681

# 以非 root 运行（/data 需可写）
RUN useradd --create-home --uid 10001 wsctl && mkdir -p /data && chown wsctl:wsctl /data
USER wsctl

CMD ["wsctl", "serve"]
