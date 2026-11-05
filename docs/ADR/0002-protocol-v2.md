# ADR-0002: 可恢复终端协议 v2

- 状态：已接受
- 日期：2026-11-04
- 决策者：wsctl maintainers
- 相关：ROADMAP-0.2.0 §5.2、§5.3

## 背景

0.1.x 的 WebSocket 只有两种帧：原始终端字节与 JSON 控制。恢复模型是「attach 时
全量回放 scrollback」+ `nudge_repaint()` 两次 SIGWINCH。它无法回答三个问题：

1. 客户端到底收到了哪个字节？
2. 服务端丢掉了哪一段？
3. 全屏应用（vim/htop）的屏幕能否在不依赖应用重绘的情况下恢复？

## 决策

新增 `/ws/v2`，子协议 `wsctl.terminal.v2`：

```
S→C: kind=1 | seq u64 | flags u8 | payload
C→S: kind=2 | flags u8 | payload
控制: UTF-8 JSON，必须含 {"v":2,"type":...}
```

- `seq` 是会话输出流的全局字节偏移；
- 客户端 `ack(seq)` 表示该偏移之前已写入 xterm；
- 服务端 `ScrollbackRing` 暴露 `[base_seq, end_seq]`；
- `last_seq` 在窗口内 → 精确增量；低于 `base_seq` 或 generation 不一致 →
  屏幕快照（tmux capture-pane / pyte shadow screen）；
- 慢客户端丢帧 → `gap {from,to,reason}`，客户端可请求 `snapshot-request`；
- 快照以 `FLAG_SNAPSHOT` 的二进制帧发送，控制消息只携带元数据。

关闭码保留 0.1.x 语义，新增：

- `4426` 协议版本不兼容（致命）；
- `4427` 快照不可用（可重连/回退）。

## 备选方案

| 方案 | 结论 | 原因 |
|---|---|---|
| 继续全量回放 | 拒绝 | 4 MiB scrollback 每次重连都重传，TUI 仍会花屏 |
| 只加 `last_seq`，不做快照 | 拒绝 | ring 淘汰头部后无法恢复完整屏幕 |
| 把 seq 放进 JSON | 拒绝 | 每帧 JSON 编解码会吃掉终端吞吐 |
| 用 WebTransport | 推迟 | 浏览器支持与部署复杂度不匹配 0.2.0 |
| 服务端保存每个客户端的屏幕 | 拒绝 | 内存与 CPU 随观众数线性增长 |

## 后果

- v2 客户端需要理解帧头、ACK、快照与 gap；CLI 与浏览器参考实现已在仓库内；
- `/ws` 保留一版兼容别名，但新功能只在 v2；
- `nudge_repaint()` 保留为快照失败时的兜底，不再是主恢复路径；
- 测试必须覆盖：断线续传、ring 淘汰、generation 失配、慢观众 gap、
  快照不可用回退。

## 安全约束

- 控制帧有大小上限（64 KiB）与速率限制；
- 输入帧上限 256 KiB，超限整帧拒绝；
- 快照/回放同样受分享撤销与只读权限约束；
- `ack` 只接受非负整数，stale ACK 不倒退。
