#!/bin/sh
# wsctl 容器入口：确保 admin 存在后再启动服务。
#
# 密码来源优先级：
#   1. WSCTL_ADMIN_PASSWORD  （推荐，由编排系统注入）
#   2. 首次启动生成随机密码并打印到日志（请立刻用 `wsctl user passwd admin` 改掉）
set -eu

# 没给子命令就默认起服务；给了就原样转发（例如 `docker run wsctl version`）。
if [ "$#" -eq 0 ]; then
    set -- serve
fi

# 只在「确实是启动服务」且「数据库还不存在」时注入一次初始密码，
# 之后改密请用 `wsctl user passwd admin`，不要覆盖环境变量重启。
case "${1:-}" in
    serve|start|restart)
        if [ -n "${WSCTL_ADMIN_PASSWORD:-}" ] && [ ! -f "$WSCTL_DATA_DIR/wsctl.db" ]; then
            set -- "$@" --admin-password "$WSCTL_ADMIN_PASSWORD"
        fi
        ;;
esac

exec wsctl "$@"
