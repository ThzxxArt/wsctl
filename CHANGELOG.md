# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.13] - 2026-09-23

**升级后界面「设了不生效」的根因：浏览器把旧 `app.js` 缓存住了。**

一位用户升级后右键仍是复制粘贴，把右键行为改成「弹出菜单」也毫无反应。代码与包都
是对的（wheel 里 `openTermMenu`/`term-menu` 齐全，全新 profile 实测菜单正常弹出）；
真正的原因是 `StaticFiles` **完全不发 `Cache-Control`**，浏览器于是按「启发式缓存」
自行决定何时回源。结果是**新的 `index.html` 配旧的 `app.js`**——新的下拉框在页面上，
而绑它的那份 JS 不在，于是怎么设都没用。

这也是测试没拦住的原因：浏览器用例每次都是全新 profile，**从不经过缓存路径**。

### Fixed

- **可变资源强制再验证**：`index.html` / `app.js` / `app.css` / manifest 一律
  `Cache-Control: no-cache, must-revalidate`。升级后刷新即拿到新前端，不可能再出现
  「界面是新的、逻辑是旧的」。
- **vendor 包改为长缓存**（`public, max-age=31536000, immutable`）：它们按版本号
  发布、内容永不原地变更，硬缓存既安全又省流量。
- 新增用例逐条断言两类资源的缓存头，防止将来被改回去。

### Verified

420 unit · 17 browser · 10 e2e 全绿；实测 `/`、`/static/app.js`、`/static/app.css`、
`/static/index.html` 均为 `no-cache, must-revalidate`，`/static/vendor/*` 为
`immutable`。

## [0.1.12] - 2026-09-23

**根治「跑大量输出时终端一跳一跳」。** 一位用户在 wsctl 网页终端里跑一个输出极多的
程序，终端内容卡住不动、一下一下地跳；同一时刻开着的空闲会话毫无异常。症状是
「踢断 → 重连 → 清屏 → 回放 → 又灌满 → 再踢断」的循环在屏幕上的样子——0.1.8 把
「无预警黑屏」修掉了（所以不再变黑），但**踢断本身**一直还在。

`DESIGN.md` 里写的是「慢消费者达到上限时**丢弃旧帧或断开**」，我们只实现了「断开」
这条。而终端是**当前视图**，不是档案馆：远端跟不上时，丢最旧的帧是正确的，把人踢掉
是过度惩罚——慢一点就该看不全，而不是被断开。

### Fixed

- **慢消费者丢帧保连接，不再被踢。** 出站队列接近上限时淘汰**最旧的终端帧**（控制帧
  永不淘汰：解释丢了什么的那条必须到得了），连接始终维持。因此「掉线 → 重连 → 清屏
  → 回放」这条循环从根上消失，屏幕不再跳动。
- **丢帧必须说出来。** 每轮丢弃会给一次提示「输出过快，已省略部分较早内容（会话仍在，
  连接未断）」，队列排空后允许再次提示。静默丢历史不可接受——这与输入路径「丢弃的按键
  必须报给用户」是同一条规则。
- **新增 `wsctl_client_frames_shed_total` 指标。** 丢帧现在是**好结果**（它换来了
  连接不断），值得观察：它高而 `wsctl_clients_evicted_total` 低是健康的；两者同时
  上涨才不正常。
- **内存驱逐仍如实报 4410**（会话缓冲超上限这一条通路仍会移除连接），而不是谎称
  「正常关闭」。`Client` 协议因此显式带上 `close_code`——它本就是「这个客户端如何
  收场」契约的一部分。

- **schema 迁移在多实例下不再炸库。** `_migrate()` 用 `PRAGMA table_info` 判断后再
  `ALTER TABLE ADD COLUMN`，两个实例共享同一 data_dir（SO_REUSEPORT 热切换的既定
  用法）时会同时看到「列不存在」而一起加，后者以 `duplicate column name` 让**整个
  打开失败**。新增 `end_reason` 那一列第一次让这个竞态稳定复现。现在把 ALTER 包进
  幂等处理，抢输的那一方视为成功。新增 8 线程并发开库用例。

- **丢帧对齐到 ANSI 序列边界——用流式状态，不是无状态扫描。** 首版用
  `ends_inside_escape(单帧)` 判断安全切点，而 PTY 一次读 64KB 会把 `ESC[31m` 切成
  `ESC[3` + `1m`：第二帧以 `1m` 开头，无状态扫描会判它「纯文本、可安全切」——正好
  反了。现在 `core/ansi.py` 提供 `AnsiTracker` 流式跟踪「是否在转义序列中」，
  `WsClient` 与 `Scrollback` 逐帧喂给它，只有**状态回到序列外**的帧才是安全切点。
  同时修正 `skip_partial_prefix`：它连**完整的开头序列**也丢，白扔一个颜色。
  > 全量回归又从该状态机里挖出两个 off-by-one：`ESC c`（两字节）被当成还要等一个
  > 字节、`ESC ( B`（三字节）被当成还要等两个，于是**同一条字节流换个切分方式就得出
  > 不同结论**；以及 `ESC ]` 被拆成 `ESC` + `]` 时，`]` 被当作普通字符吃掉，OSC
  > 开头直接丢失。现在以**分片不变性**为准：5 个随机种子 × 5 万条随机字节流 ×
  > 随机切分 = 25 万次验证零违例，且与无状态版在边界输入上完全一致。
- **e2e 不再被开发者的代理劫持。** 导出过 `https_proxy` 的 shell 会让 `httpx` 把
  `http://127.0.0.1:…/healthz` 也送进代理，代理回答 502——**完全健康的本地实例看起来是
  死的**。这正是 `core/net.opener_for` 当初为 wsctl 自己的 CLI 修掉的陷阱，测试侧却
  一直没有；滚动迁移用例因此报出一串 `status 502`。现在本地探测一律用
  `trust_env=False` 的客户端，`_clean_env` 同时剥离代理变量。（实现中修掉一处 `deque mutated during iteration`：
  删除后继续迭代同一 deque 会抛 `RuntimeError`，正好发生在需要二次丢弃的路径上）。 PTY 一次读 64KB，可能把 `ESC[31m` 切成两帧；
  丢掉前半帧会让残余流从 `31m` 开始，终端把它当**文字**解析——`vi`/`htop` 这类全屏
  程序于是花屏到下次全量重绘为止。现在丢弃时会继续向后丢到最近的序列边界，残余流
  一定从终端认为「新的开始」处起步。新增 `core/ansi.py`（只解析 eviction 需要的那
  部分语法）与 12 条边界用例。
- **回放起点同样对齐。** scrollback 淘汰后留在队首的字节就是重连回放的开头；若被淘汰
  的部分在一个转义序列中间结束，回放本身就是坏的。淘汰后现在会重新锚定起点。
- **会话内存超限先丢积压、再移除连接。** 此前直接踢掉积压最大的客户端——这正是刚从
  每客户端路径上移除的「过度惩罚」，只是换了个触发条件。现在先让客户端 `shed_oldest`
  交出积压，仍超限才移除（并报 4410）。
- **全屏应用用例**：`vi` 打开后灌入大量彩色转义序列，断言渲染结果无替换字符（U+FFFD）、
  无 `[31m` 泄漏成文本、无裸 `ESC`。此前这条风险**零覆盖**。

### Verified

新增两条针对性用例：**慢观众掉不了线**（200 帧灌入，连接仍在、旧帧被丢、提示已发）与
**控制帧不被丢弃淘汰**；三条旧契约用例改为新语义。389 unit · 16 browser · 10 e2e ·
1 slow · 16 guards 全绿。

## [0.1.11] - 2026-09-23

**主题：让在线终端能重启自己。** 一位远程用户在 wsctl 网页终端里执行
`pipx upgrade wsctl && wsctl stop … && wsctl start …`，服务停了再也没起来，
家里那台机器从此打不开。根因是架构性的：`wsctl stop` 会关闭实例，而实例关闭
会结束它托管的会话——**执行这条命令的 shell 也在会话里**，于是 `&&` 后半段永远
跑不到。产品从不支持「重启自身」，且失败是静默的。

### Added

- **`wsctl restart --rolling`：先起后停，可在 wsctl 自己的终端里安全执行。**
  替代实例先接管端口，`/healthz` 自报 pid 让闸门确认**是继任者在应答**（两端
  共享端口时前任也会答 200，只看状态码会提前放行），确认后才退休前任。
  原实例带 `--reuse-port` 时**零停机**；不带时端口无法共享（内核如此：SO_REUSEPORT
  要求两端都设置），会**先告知**「本次为一次性迁移，有毫秒级窗口」再切换，此后
  滚动重启即零停机。
- **终端右键菜单**：复制选区 / 粘贴 / 全选 / 查找 / 清屏 / 字号 ± / ZMODEM /
  断开 / 终止。右键行为三模式可在设置里切换（菜单 · 快捷复制粘贴 · 始终粘贴）。
- **快捷键一览的可靠性图例**（✅ 可靠 / ⚠️ 部分浏览器可用 / ❌ 浏览器占用）。

### Fixed

- **`wsctl stop`/`restart` 不再锯断自己坐的树枝。** 沿 `/proc` 的 ppid 链检测
  自身是否运行在该实例托管的会话内，默认**拒绝**并说清后果（服务会停、这条命令
  的 shell 会一起没、`&&` 后半段跑不到），给出两条出路：改在 SSH 执行，或用
  `wsctl restart --rolling`。明确接受后果可加 `--i-know-this-drops-my-connection`。
- **`stop` 未在运行时幂等退出 0**（像 `rm -f`）。此前返回 1，`wsctl stop … && wsctl
  start …` 的 `&&` 会静默跳过启动——正是这次事故的帮凶。
- **`stop` 找错端口时首句给修正命令**（`未在 7681 端口上没有实例；发现 pid X 正在
  7682 运行 / 你是想找它吗？`），而不是干巴巴一句「未在运行」。
- **复制粘贴改用 `Alt+C` / `Alt+V`**（代价：这两个键从 shell 手里被接管，readline 的
  `Alt+C`「首字母大写」等随之不可用——与 `Alt+N`/`Alt+W` 同一取舍；不接受可在设置里改回，
  或用右键菜单 / `Ctrl+Shift+Insert`，它们不抢任何键）。 `Ctrl+Shift+C` 是 Chrome/Edge/Firefox 的
  DevTools 检查器，属浏览器 chrome 级快捷键，页面 `preventDefault()` 拦不住——
  按下去打开的是控制台而不是复制。`Alt+C`/`Alt+V` 与本产品既有的 `Alt+N/W/F/S/H/L`
  体系一致，浏览器不占用；另保留 `Ctrl+C`（有选区时）与 `Ctrl/Shift+Insert`。
  `Ctrl+Shift+C/V` 仍在但标为「部分浏览器可用」。
- **`?` 不再抢终端输入。** 它此前无条件吞掉按键，导致 shell 里敲 `?`（如 `ls ?`）
  变成打开帮助。现在只在终端未聚焦时响应，打字时用 `Alt+?` 唤帮助。
- **帮助浮层的快捷键显示为 `Alt+C` 而非 `alt+c`**，并标注哪些组合键会被浏览器占。
- **停止/回收/终止前先广播原因**（`服务正在停止，会话即将结束` 等），界面不再是
  无预警黑屏。
- **`/healthz` 自报 pid 与 instance_id**：滚动重启的闸门必须知道**是谁**在应答。

### Changed

- `--daemon` 与 `--reuse-port` **不再互斥**。此前两者水火不容，导致零停机重启在
  托管生命周期下完全无法使用——这正是「重启自身」做不到的直接原因。pid 文件现在
  记录 `reuse_port`，`wsctl start --reuse-port` 是可托管的实例。手工多实例热切换
  （直接 `serve --reuse-port`）仍是无托管语义，不变。
- `wsctl start` 新增 `--reuse-port`。首次部署建议带上，之后 `restart --rolling`
  即为零停机。
- e2e 剥离继承的 `WSCTL_*` 环境变量。开发者 shell 里泄漏的 `WSCTL_DAEMON=1` 曾让
  每个测试实例都以为自己是托管守护进程并去抢 pid 文件，两个 `--reuse-port` 实例
  因此互相撞死——与 0.1.7 的测试封闭性工作同一类缺陷，这次藏在 e2e 里。

### Internal

- `e2e ws_recv_until` 的停止标记此前会命中**回显的命令行**（`echo AFTER-ALL` 的输出
  里就含 `AFTER-ALL`），于是多数输出断言其实匹配到输入而非输出。`selfrestart`
  场景改用**不可回显的标记**（`$?`、`$((6*7))`）与固定时长排空，是目前唯一
  诚实验证「命令真的执行了」的场景。

### Verified

e2e 新增 `selfrestart` 四段全过：会话内执行 stop 被拒（exit 3 并给出出路）·
stop 幂等 · **零停机滚动重启（全程 /healthz 零失败）** · 受控迁移后同样零停机。
387 unit · 14 browser · 10 e2e · 1 slow · 16 guards 全绿，且 e2e 在带 `WSCTL_*`
污染的 shell 中运行通过。

## [0.1.10] - 2026-09-23

修复 0.1.9 发布过程中由 CI 暴露的一个**产品缺陷**：上传结果谎报。

### Fixed

- **上传取消 / 跳过不再报「上传完成」。** 原本用户拒绝覆盖同名文件后，队列走空即进入
  完成分支，状态行照样写「上传完成」——**声明与行为不符**。同一行状态还在多轮上传间
  复用，于是下一次上传的完成判定会被上一次的残留文案提前命中。
  现在按实际结果四态分开：`上传完成（N 个文件）` / `（跳过 N 个同名文件）` /
  `未上传任何文件（已跳过）` / `已取消上传` / `上传失败（N 个文件）`，并计数贯穿整批。
  取消（abort）显式清空队列，绝不落入完成分支。

### Fixed（测试侧）

- **覆盖上传用例改为等文件真实落盘**，而不是等一句可能陈旧的状态文案。这正是它在
  CI 上读到 `b"original-content"` 而失败的原因——`上传完成` 是上一轮被拒绝的那次
  留下的。

### Verified

browser 连跑 3 次 3/3（12 用例）稳定；378 unit · 9 e2e · 1 slow · 12 guards 全绿。

## [0.1.9] - 2026-09-23

**零产品变更。** 这一版只做测试加固，把 0.1.8 发布后 CI 暴露的两个**测试缺陷**
收进发布包。0.1.8 的产品代码经过逐行核对未受影响——`fs.write_text_file` 显式
UTF-8 编码、`read_text_file` 显式命名解码，Windows 上功能正常。

发这一版有两个理由：0.1.8 发布后落在 main 上的两条修复本就游离于任何发布之外；
且 `v0.1.8` 这个 tag 的 CI 记录会永久停在 8/9。

### Fixed（测试侧）

- **测试套件的文本 I/O 全部显式指定编码**（8 个文件 46 处）。`Path.read_text()` /
  `write_text()` 不传 `encoding` 时回落到 locale，而默认 Windows 控制台是 cp1252，
  于是两处往返中文的断言失败：`write_text("héllo 世界")` 抛 `UnicodeEncodeError`，
  `read_text() == "after 内容"` 读出乱码。
  **这是本项目同一类缺陷的第四次复发**——0.1.5 修的是产品 stdio，测试里还留着。
  因此按「修类不修实例」处理：不是补那 2 处，而是 46 处一起改。
- **上传取消用例不再与真实上传赛跑。** 原用例先断言进度条可见、再点「取消」，而在
  快的 runner 上上传会在两者之间完成，进度条随即自行收起，Playwright 对着消失的
  元素重试点击直到超时。上一次的绿是运气。两处改动：竞态路径容忍「进度条已收起」
  （这本就是它注释里写明的两种正确结果之一）；另加一条**确定性**用例，用路由拦截
  把上传挂起，此时进度 UI 不可能自行结束，取消必然落点，并断言传输被中止、磁盘
  无残留。

### Verified

378 unit · 12 browser · 9 e2e · 1 slow · 12 guards，以及 windows-smoke 实跑的那
132 条；两条上传用例连跑 3 次 3/3 稳定。

## [0.1.8] - 2026-09-23

**主题：状态可见、操作完整、故障不静默。** 一次「补全」型发布——修掉六处
「系统知道，但用户看不见 / 行为与声明不符」，并把文件面板与会话历史这两块
「能用但不完整」补齐。无 schema 变更。

### Fixed

- **【事故修复】终端「卡住变黑」的完整链路已断开。** 一条超大输出（跑测试套件这类）
  就能让浏览器终端假死并黑屏，且**日志、指标、界面三处都无痕迹**。链路是：
  出站队列 512 条 / 积压 8MB 触发 `ClientGone` → `_broadcast` **静默吞掉**（无日志无
  指标无提示）→ 以 **1000「正常关闭」**收尾 → 浏览器认为一切正常、自动重连 →
  `term.reset()` **先清屏**再等回放 → 回放途中再次被打断 → **持续黑屏**。
  四处根治：慢消费者掉线**先告知再断**（`final_notice`，控制帧不占字节预算）、
  **记日志 + `wsctl_clients_backpressure_dropped_total` 指标**、新增**专用关闭码 4410**
  `CLOSE_SLOW_CONSUMER`（可恢复，进 FATAL 会阻止正确重连）、**清屏推迟到回放首字节**
  （重连失败保留旧画面，而不是留下一块黑；空回放时在 `attached` 兜底清理，避免下一次
  真实输出冲掉仍然准确的旧画面）。
  > 发布前最后一轮 review 复查发现此处**修得不彻底**：`4410` 当时只定义、分类、写进
  > 文档，**线上发的仍是 1000**——「客户端以为一切正常」这一环没断。现在由 `WsClient`
  > 自报死因、收尾如实上报；同时 `WsClient.close()` 此前在已关闭时提前返回、不放终止
  > 哨兵，导致写协程永久阻塞、每次掉线白等 2 秒，且其后排队的提示永远发不出去。
- **【事故修复】关闭原因不再被 socket 抢先关掉吞掉。** `deny()` 原本先 `put(解释)` 再
  立刻 `websocket.close()`，出站写协程来不及发出就断了——**关闭码送到了，人话没送到**。
  分享被撤销、登录失效、空闲超时、限速断开**全部**走这条路，全部都会丢解释。现在
  关闭推迟到写协程排空之后。
- **【0.1.7 遗留】分享撤销后，文本输入路径不立即拒绝。** `share_revoked()` 只在二进制
  帧和 5 秒周期复查处检查，`{"type":"input"}` 完全跳过——被撤销的可写分享**还能继续
  敲命令最长 5 秒**。现在 input / resize / 二进制三路一致即时生效。
- **【0.1.7 遗留】API 类命令不跟随正在运行的实例。** `wsctl session list` 等仍盲目信任
  `wsctl login` 的陈旧缓存地址，于是对着下线端口报「cannot reach」而健康实例就在旁边
  ——这正是 0.1.7 给 `doctor` 修过的同一缺陷，只是漏了其余命令。现在 `--url` 显式优先，
  否则跟随正在运行的实例并明说缓存地址未使用。
- **输入限速不再踢断连接。** 超限的一段输入是**丢弃并提示**（与只读分支同语义），
  只有持续轰炸（连续 3 次）才断开，且用专用关闭码 `4429`。此前一超限就 `return`
  关掉整条 WebSocket——而令牌桶是**每连接独立**的，于是粘贴一大段 → 断线 →
  自动重连拿到新桶 → 再超限 → **重连风暴**。
- **关闭码集合只有一份定义。** `core/closecodes.py` 是唯一来源；浏览器无法
  import Python，`static/app.js` 镜像同一份**并由 `tests/test_closecodes.py`
  断言两者相等**。此前三处各写一份，`4401` 在 CLI 里算致命、在浏览器里不算，
  没有任何机制发现这种分叉。
- **会话重命名会广播给所有已连接客户端。** 此前只写库与内存，两个开着同一会话
  的标签页会一直显示不同名字，直到各自恰好重连。
- **内存背压释放连接时会先告知。** 先发 `{"type":"evicted"}` 再断开。产品早已
  承诺「丢弃的按键必须报给用户」（`pty.py`），却没把同一条原则贯彻到客户端驱逐。
- **运营提示不再写进终端缓冲。** 「会话为只读」「输入速率超限」这类提示走 Toast
  与状态条。写进屏幕意味着它们进入 scrollback，并在**每次重连回放时再出现一遍**，
  像是 shell 自己打印的。
- **`wsctl doctor` 不再创建数据库。** 用只读连接（`file:...?mode=ro`）探测，
  库不存在时报告「尚未初始化」。一个体检命令改写它正在检查的目录是陷阱。
- **上传是原子的。** 先写 `.<name>.wsctl-upload` 侧车再 `os.replace`。此前用
  `O_TRUNC` 直写目标，上传 80MB 的过程中**并发下载会读到半截文件**。

### Changed

- **「热更新」拆成三档，八项此前标注不准确。** 现在是 `立即生效` / `新建时生效` /
  `需重启`。`scrollback_bytes`、`session_memory_limit`、`session_max_clients`、
  `idle_timeout`、`max_life`、`input_rate_limit`、`input_rate_burst`、`audit_input`
  此前标着「热更新」，实际只影响**此后新建**的会话/录制/连接——改了
  `session_memory_limit` 的人以为生效了，其实没有。
  `tests/test_config.py::test_every_setting_is_classified_exactly_once` 现在
  强制每个配置项都被归类且只归一类，新配置无法再悄悄落进错的档。
- **`session_sliding_ttl` 热重载真正生效。** 它的生效点是 `Store.sliding_ttl`
  而不是 `Settings`，此前 reload 只改了 Settings，那个字段一直没被重载触及。
- **会话列表的 owner 查询批量并出事件循环。** 此前 `_serialize` 对每个会话调一次
  `user_get_by_id`——64 个会话 = 64 次同步 SQLite 往返**在 event loop 里**。
- **会话结束有原因，不只是一个枚举。** `status` 说「发生了什么」（killed），新增的
  `ended_reason` 说「为什么」（被管理员终止）；`/api/sessions/{sid}/detail` 附带完整
  **时间线**（创建 / 连接 / 断开 / 终止…，来自审计表）。
- **管理员概览给出关键指标**而不是把 `/metrics` 文本截前 40 行——那会在一个序列中间
  断掉，比不给更糟。
- **上传先 `fsync` 再 `os.replace`。** 只做 rename 的话，紧接着崩溃可能留下一个名字
  正常、内容为空的文件，看起来像是完整的。
- **`term_session_upsert` / `set_status` 全部移到工作线程。** 0.1.2/0.1.4 声称
  「数据库读写全部出事件循环」，这几处漏了。
- **回放缓冲按块发送。** 此前每次 attach 都 `b"".join(整个缓冲)`，4 MiB 缓冲
  就是每次重连 4 MiB 的纯拷贝。

### Added

- **文件面板补全为完整文件管理。** 新建目录、重命名、删除、右键上下文菜单、
  按名称过滤、分页（此前硬停在 2000 条，第 2001 条永远够不着）、文本预览与
  就地编辑、拖放到**具体目录**、**上传进度条与取消**（`fetch` 没有上传进度，
  这里特意用 `XMLHttpRequest`）。删除只允许文件或空目录——递归删除是**刻意不提供**
  的，一次误点不能清空家目录。
- **会话历史不再凭空消失。** `term_sessions` 表一直写着 `status`（running /
  stopped / expired / killed）和保留策略，却没有任何地方读过它。新增
  `GET /api/sessions/history`、`GET /api/sessions/{sid}/detail`，Web「会话」弹窗
  分「运行中 / 已结束」两个页签。管理员看全部，普通用户只看自己的（`argv`/`cwd`
  可能含敏感路径，见 SECURITY.md）。
- **管理员「概览」页签。** 实例列表、会话/客户端/缓冲统计、最近 20 条审计、
  最近结束的会话。「这台机器健康吗」此前要打四个接口。
- **`?` 快捷键帮助浮层。** 快捷键一直可编辑，但没有任何地方告诉用户它们是什么。
- **终端搜索升级**：**全部命中高亮**（新增 vendored `@xterm/addon-search` 0.16.0，MIT）、
  实时命中计数、区分大小写 / 全词 / 正则三个开关、无效正则明确报「无效模式」、
  修掉 `translateToString(true)` 吃掉行尾空格导致 `"foo "` 永远搜不到的问题。
- **`api()` 加 15 秒超时**并区分超时/网络/鉴权/服务端错误；Toast 可点击关闭，
  错误类**常驻直到用户关**（此前 3.2 秒自己消失）；管理面板重绘保留滚动位置与输入焦点。
- **管理表排序/翻页改为替换式重绘。** 原本每次点击表头/翻页都是 `appendChild`，
  点一次就**多堆一张表**；现在重绘前先清空，且只清表格容器、不动旁边的工具栏。
- **`/api/sessions/{sid}/detail` 两条路径字段同形。** live 缺 `argv`/`cwd`/`duration`，
  history 缺 `pid`/`bytes`/`shared`——消费方不得不按「是否存活」分支。计划点名的
  字段现在两边都有。
- **历史「重新打开」按 `argv` 原样重建。** 原本用 `command + backend` 拼装，SSH 会话会
  400（缺 `ssh` 结构），而「顺手把 backend 去掉」的修法会把**远端命令当本地命令执行**。
  现在走 `POST /api/sessions/{sid}/reopen` 复用记录的 argv；SSH 项在界面上明示需重新
  填写目标，而不是假装能重开。
- **删除等不可逆操作需输入名称确认。** 删除文件、录制、用户都不再是「确定 / 取消」——
  必须照着提示把条目名称敲回去，确认按钮才解锁。递归删除本就刻意不提供，这是第二道闸。
- **用户表与录制表支持排序 + 分页**（点击表头切换升/降序，25 条一页）；此前只有审计有
  「加载更多」，上百行的用户表是一堵没有顺序的文字墙。
- **配置徽章带 tooltip**：三档各自的含义悬停可见——「新建时生效」那条尤其重要，否则
  它看起来只是「热更新」的弱化版，用户会以为改动已经落地。
- **文件面板支持按类型过滤**（全部 / 仅目录 / 仅文件）。
- **快捷键一览可复制**：点任意组合键复制该行，或「复制全部」。
- **a11y 达 WCAG AA（已实测并固化为测试）**：`prefers-reduced-motion`、触控目标 ≥ 44px；
  表单控件边界改用专用的 `--border-input`。审计实测发现两套主题的控件边框只有 1.5:1，
  远低于 1.4.11 要求的 3:1——纯装饰分隔线可豁免，但**控件边界就是辨识控件的依据**。
  `tests/test_contrast.py` 现在逐对比值断言，改色再退化会直接红。
- **`wsctl_pty_input_dropped_total` 之外新增** `wsctl_input_rate_limited_total`
  与 `wsctl_clients_evicted_total` 两个指标。

### Internal

- 新增 vendored 第三方资产：`@xterm/addon-search` 0.16.0（MIT），许可全文已并入
  `src/wsctl/static/vendor/THIRD_PARTY_NOTICES.txt`。
- **`create_app` 从 942 行拆到 291 行。** 30 个路由移入 `server/routes/`（10 个
  关注点模块），请求体进 `server/models.py`，DI 进 `server/deps.py`，跨实例对账进
  `server/maintenance.py`。行为零变化，拆分前后 337 + 4 + 1 + 8 全绿对拍。

## [0.1.7] - 2026-09-22

Follow-up to 0.1.6 for `wsctl doctor`, the one lifecycle command that had no
way to say *which* instance it meant and no idea which one to talk to. No new
features, no schema change.

### Fixed

- **`wsctl doctor` gained `--host`/`--port`.** Every other lifecycle command
  takes them; doctor did not, so `wsctl doctor --port 7682` died with
  "No such option: --port" on the exact invocation a user reaches for when two
  instances are running. It resolves the target with the same narrowing
  semantics as `status`/`logs`/`stop`.
- **`--config` no longer becomes ambient process state.** `_settings_from`
  assigned `os.environ["WSCTL_CONFIG"]` permanently, so a later call without
  `--config` in the same process silently kept reading the previous file. The
  override is now scoped to the call and restored afterwards.
- `doctor` loads its settings once, with the target the user named. It used to
  load them twice just to pick up `--host`/`--port`, which repeated the
  `os.environ` side effect above and left the early checks reporting the wrong
  address.
- **The 服务器 row probes the instance you are running.** It used to probe the
  URL cached by an old `wsctl login`, so a healthy instance on 7682 reported
  `cannot reach http://127.0.0.1:7720: timed out` and the box looked dead. The
  order is now: an explicit `--url` wins, then the running instance's
  `/healthz`, and only then the cached login URL — which is labelled with where
  the address came from and how to clear it (`wsctl logout`).

### Testing

- The suite can no longer read the developer's own `~/.config/wsctl/` or their
  exported `WSCTL_*` variables. This class of defect has now caused a
  wrong-green three times (a `credentials.json` in 0.1.5, default-value
  assertions in 0.1.6, and the four new doctor cases here), so it is fixed at
  the root: an autouse fixture gives every test a private config and data
  directory.

  The canary was the first thing this review checked, and it was hollow:
  `assert json.dumps(creds) != "{}" or creds == {}` is a tautology that cannot
  fail whatever leaks, its neighbour only looked for a `ghp_` prefix so any
  other credential slipped through, and its docstring claimed to plant a marker
  it never planted. All three are corrected: the sandbox must live outside the
  caller's home and must start completely empty. A negative control with a
  deliberately ordinary, non-`ghp_` token is now caught.
  Verified both ways -- with the fixture disabled a canary test
  reads the real cached login and fails; with it enabled the same test passes.
  A canary suite (`tests/test_isolation.py`) now fails if the fixture is ever
  weakened.

  Tests that write to a specific path still say so explicitly (and the browser
  suite must, since a spawned server is a subprocess and fixtures do not reach
  it); what is gone is the need to *defend* against the developer's shell.

[0.1.7]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.7

## [0.1.6] - 2026-09-22

Operational-usability release. Four bugs behind one bug report — "you hardcoded
7681", "the web page says my username or password is wrong", "doctor shows
502" — plus a fifth in the same family found while fixing. No new features, no
schema change.

### Fixed

- **Lifecycle commands now follow the running instance.** `wsctl start --port
  7682` followed by `wsctl status` / `logs` / `stop` / `reload` / `restart`
  looked at the *default* port and reported 未在运行 / 没有日志文件 for
  `wsctl-7681.log` while the instance was serving right there — `status` even
  printed "发现其他实例：… 0.0.0.0:7682" and then refused to use it. An instance
  actually running in this data directory now wins over "the default address",
  and the command says which one it followed. A named flag only ever *narrows*
  the search — `--port` restricts to that port, `--host` to instances bound
  there, both to the intersection (a pid file is keyed by port alone, so
  `--host 127.0.0.1` used to be handed the `0.0.0.0` instance). With more than
  one candidate it lists them and asks for `--port`; with none it says 未在运行
  instead of silently starting something else.
- **`--admin-password` is never silently ignored.** The bootstrap returns early
  when the database already has users, so the flag did nothing; and because the
  warning only reached the detached child's log, it read exactly like "your
  password is wrong" when the user then could not log in. The parent now warns
  on the terminal before spawning and names the command that does change the
  password. With no enabled admin left the value recovers the instance instead
  of leaving it unreachable.
- **A local wsctl is no longer routed through `http_proxy`.** `urlopen` honours
  the proxy unconditionally, so a WSL/VPN/Clash-style environment sent even
  `http://127.0.0.1:7682/healthz` to the proxy, which answered 502 and made a
  healthy instance look dead. This affected `doctor`, `status` and every
  `ApiClient` command (`login`, `connect`, `session`, `audit`, `config reload`).
  Loopback targets go direct; remote targets keep the proxy.
- `wsctl doctor` gained `--host`/`--port`, and its "服务器" row now probes **the
  instance it just resolved** instead of the URL cached by an old `wsctl login`.
  A stale credential made a perfectly healthy instance report
  `cannot reach http://127.0.0.1:7720`, which read like the server was down.
  An explicit `--url` still wins; with nothing running the cached URL is used
  and the row says where the address came from and how to clear it
  (`wsctl logout`).
- `wsctl doctor` reports the address the instance is really listening on
  (`0.0.0.0:7682`) instead of the default `127.0.0.1:7681`, and lists any
  other instances it finds rather than flatly claiming nothing is running.
- `wsctl restart` without `--port` no longer drops the port from the child argv,
  which used to bring the replacement up on the default port.

### Added

- `wsctl user passwd <name> -p <password>` for a one-command, non-interactive
  password reset — the way out when someone is locked out of the web UI.

### Testing

- Fourteen new tests pin the instance resolution, the `doctor` probe order (single instance auto-follows, two
  instances are ambiguous, a named flag narrows rather than broadens), the
  proxy bypass (behaviourally: a real local server must answer while a bogus
  `http_proxy` is set), the loud `--admin-password` warning and the lock-out
  recovery.

[0.1.6]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.6

## [0.1.5] - 2026-09-22

Windows portability and test-independence release. Two of the three fixes below
are real product bugs that made the CLI unusable on Windows; the rest make the
suite mean what it says on every platform. No new features, no schema change.

### Fixed

- **`import wsctl.core.pty` no longer crashes on Windows.** `Pty.terminate`
  declared `sig: int = signal.SIGHUP` as an *annotation default*, which Python
  evaluates at definition time -- and `signal.SIGHUP` does not exist on
  Windows, so merely importing the module raised `AttributeError`. The signal is
  now resolved once into `DEFAULT_TERM_SIGNAL` (`SIGHUP` where it exists,
  `SIGTERM` otherwise).
- **Chinese output no longer crashes a default Windows console.** Such a
  console is cp1252, so writing the help text or `wsctl doctor` through it
  raised `UnicodeEncodeError` before anything appeared. The CLI now
  reconfigures stdout/stderr to UTF-8 with a replacement fallback at startup: a
  terminal that truly cannot render a glyph degrades to `?` instead of dying.
- `fs.relative_to` returns POSIX separators on every platform. Those strings
  land in audit payloads and share links, so the same upload used to look like
  `a\b.txt` on one host and `a/b.txt` on another.

### Testing

- `test_session_list_json_is_machine_readable` is hermetic: it passed locally
  only because the developer's `~/.config/wsctl/credentials.json` gave the CLI
  a URL to fall back on, and failed on a clean runner. It now isolates
  `XDG_CONFIG_HOME` and passes `--url` explicitly.
- The PTY input-drop tests stub the write-buffer drain instead of filling a
  real PTY after `SIGSTOP`. How many bytes the kernel soaks up first is
  platform-specific (macOS takes far more than Linux), so the old version was
  measuring the kernel rather than wsctl.
- The browser rename step asserts the outcome (tab label and server-side name)
  rather than "a PATCH must be in flight while this context manager is open",
  which was timing-dependent, and guards a `querySelector` that can legitimately
  be null while the admin table renders.

[0.1.5]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.5

## [0.1.4] - 2026-09-22

Reliability and usability release. Every promise the product makes now holds
across a restart, a broken disk and a broken config edit; the CLI can log in
with 2FA and script against JSON. No new terminal protocols.

### Added

- **CLI can log in to a two-factor account.** `wsctl login --totp 123456`, the
  `WSCTL_TOTP` environment variable, or a prompt when the server asks for a
  code. Previously a 2FA-enabled account made every CLI command unusable.
- **Share links survive a server restart.** The token, its expiry and its
  writable flag are persisted with the session (`term_sessions.share_*`,
  schema version 4) and rehydrated verbatim on adoption, so a QR code that was
  already handed out keeps working — the same promise the session itself makes
  on the tmux backend. Renaming a session no longer invalidates its link.
- **Minimum password policy** (non-empty, at least 8 characters), enforced in
  one place (`core/passwords.validate_password`) at the storage boundary, in
  `user_admin`, and therefore by both the HTTP API and the CLI. Existing
  passwords are *not* force-changed; `wsctl doctor` warns instead of locking
  anyone out.
- **Log rotation** for the background lifecycle. `log_file` / `log_max_bytes` /
  `log_backup_count` rotate by copy-and-truncate, so the append-mode descriptor
  `wsctl start` hands its child stays valid across a rotation (rename-based
  rotation would leave the daemon writing into the rotated file forever).
- **Web new-session dialog** with name, command, working directory, backend and
  a structured SSH target form (host / user / port / identity / `ssh -o`
  options). `Alt+N` still opens a default shell with one keystroke.
- **Web admin "config" tab**: the effective configuration, each key labelled
  `热更新` or `需重启`, plus a reload button that reports exactly what changed
  *and* what failed to parse.
- **`wsctl completion install|show [bash|zsh|fish|powershell]`**. Typer's
  `--install-completion` only ever installs bash; this covers the rest and
  picks the shell from `$SHELL` instead of guessing bash.
- `GET /api/config` (admin) — the read-only effective-configuration view behind
  the new tab.
- CLI `--json` on `session list|new|rename`, `user add|list`, `audit` and
  `config show|get`; `wsctl audit` gained `--event` / `--user-id` / `--ip` filters;
  `wsctl session list` now shows the owner's **username** (and the backend and
  liveness) instead of a bare numeric id.
- `wsctl logs` reads across rotated backups, so a rotation never hides history.
- `wsctl doctor` additionally reports free disk space on the data partition,
  the password policy in force, the log file and its rotation threshold, and
  the system clock (which TOTP depends on).
- Metrics `wsctl_pty_input_dropped_total` and `wsctl_recording_failures_total`.
- Uploads can opt in to replacing an existing file (`overwrite=1`); the web UI
  asks first.
- File listings report `truncated` and `limit` when a directory exceeds 2000
  entries instead of silently returning everything.

### Changed

- **A broken config edit is no longer silent.** `reload_settings_file` returns
  `(changed, errors)`; `wsctl reload` and `POST /api/config/reload` surface the
  exact TOML/validation error and exit non-zero, and the server logs it.
  Previously an invalid value was discarded and the reload reported "no
  changes", which was indistinguishable from the reload not running.
- **`config set` preserves trailing comments** on the line it edits
  (`port = 7681  # 监听端口` stays annotated) and no longer mistakes a
  commented-out `# port = 1234` for the real key.
- **Web terminal connections are bounded.** At most `8` sessions hold a live
  socket (the most recently used ones); the rest appear in the tab bar as
  `未连接` and attach when opened. Previously every session opened a WebSocket
  and an xterm instance at page load, which fell over past a handful.
- Uploading over an existing file is refused with `409` unless the client
  explicitly opts in. Files were previously truncated without warning.
- The image in `contrib/docker` builds from the **repository root** and installs
  the local source; `docker compose` refuses to start without
  `WSCTL_ADMIN_PASSWORD` instead of defaulting to `change-me`.
- CI adds a macOS leg and a Windows import/CLI smoke job, runs the `-m slow`
  load suite, installs `lrzsz` so the ZMODEM round trip is really exercised,
  and builds the Docker image.

### Fixed

- **A session whose child never exits can no longer hang the server.**
  `TermSession._finalize` waited unboundedly on `Pty.wait()`; a daemonized
  grandchild holding the terminal open would wedge the read loop's cleanup
  forever and leak the session object. The wait is now bounded and escalates to
  `kill`.
- **A failed recording no longer claims to be recording.** When the writer hit a
  disk error it silently stopped while `is_recording` stayed true, so the UI
  showed a red dot for a file that was not being written. The recorder now
  closes itself, records the error, and `close()` can no longer race the writer
  thread into a half-closed file.
- **`import wsctl.core.store` no longer computes an Argon2 hash**, which made
  every CLI invocation — including `wsctl --version` — pay ~120ms before doing
  anything. The timing-equalising dummy hash is computed lazily; combined with
  lazy imports of the server/client dependencies, `wsctl version` went from
  ~1.05s to ~0.36s.
- **Authentication lookups no longer run on the event loop.** The WebSocket
  handshake and its periodic re-check did synchronous SQLite work per
  connection every 5s, as did the maintenance loop's leases/reaping/retention.
  They now run on worker threads, and a short-lived auth cache
  (`core/authcache.py`) turns the re-check into a dict lookup. Revocation stays
  immediate: disabling a user, changing a password or changing a role drops that
  user's cache entries at once.
- **A dropped keystroke is now reported.** When a session's child stops
  draining its terminal the write path drops input rather than grow without
  bound — but it did so *silently*, which looked like a dead keyboard. The
  client is told once (`终端输入过快，部分按键已丢弃`) and the drops are counted.
- **A stale `attached` message can no longer revert a rename.** The tab label
  is protected by a rename generation counter, so a reconnect that echoes the
  pre-rename name does not silently undo what the user just typed.
- **`promptDialog`/`confirmDialog` are singletons.** A second invocation while
  one was open overwrote the input the user was typing into; that is how a
  rename could send the *old* name back to the server and look lost.
- **A finished ZMODEM transfer always releases the keyboard.** The input lock
  was only cleared by the transfer's end event, so an aborted transfer left the
  terminal accepting no input at all. An inactivity watchdog now force-releases
  it (and `on_retract` does too).
- `wsctl connect` survives non-JSON text frames (a proxy or middlebox injecting
  one used to tear the connection down with a traceback).
- **Maintenance reconciliation can no longer mis-kill a live session.**
  `term_session_stop_missing` takes the in-memory session set as its liveness
  list; running its `UPDATE` on a worker thread after taking that snapshot
  widened the window to thread-scheduling latency, so a session created in
  between would be written as `stopped` while running (and never adopted again
  after a restart). The snapshot and that one statement stay in a single
  synchronous stretch on the loop; only snapshot-independent writes are
  threaded.
- **Argon2 hashing no longer runs under the store lock.** `user_create` and
  `user_set_password` hashed while holding the single store mutex, serialising
  every other database operation — including authentication — behind one
  ~100ms CPU-bound hash. The hash is now computed before the lock is taken.
- **Audit input records only what actually reached the shell.** Keystrokes
  dropped by write backpressure (or a session that had already exited) used to
  be audited as if the command had run. Dropped input is now reported to the
  user and simply not recorded.
- The terminal connection indicator follows the *visible* tab: a background
  tab reconnecting or being suspended no longer rewrites it to
  "连接中"/"空闲".
- Metrics named `_total` (`wsctl_audit_dropped_total`,
  `wsctl_audit_write_errors_total`, `wsctl_pty_input_dropped_total`,
  `wsctl_recording_failures_total`) are now exposed with `# TYPE … counter`.
  They were declared `gauge`, which makes `rate()` reject them. The two new
  ones are also genuinely monotonic — a per-session sum would have gone *down*
  whenever a session ended, showing up as counter resets.
- Metric label values are escaped for the Prometheus text format (a `"` or
  newline in a label produced invalid exposition output).
- Scrollback eviction no longer leaves an orphaned UTF-8 continuation byte at
  the head of a replayed screen (rendered as a replacement glyph).
- `wsctl doctor` no longer imports the hashing stack just to print a table.

### Testing

- New suites for the password policy, the auth cache (including revocation
  guarantees), copy-and-truncate log rotation, recording-failure honesty,
  scrollback UTF-8 alignment and metric label escaping; extended suites for
  share persistence (including "a rename must not invalidate the link"),
  the bounded `Pty.wait`, PTY input drops, the upload overwrite guard, the
  config endpoint and reload errors, TOTP/`--json`/comment-preserving
  `config set`, and `completion show`.
- The browser suite covers the new-session dialog (including the SSH-required
  validation), the config tab, the upload overwrite prompt, the bounded
  connection budget, and a **real ZMODEM `sz`/`rz` byte-exact round trip**.
- `scripts/e2e/run_all.py` asserts that a share link keeps working across a
  full server restart, and reports the child's log when a scenario fails to
  start.

### Upgrade notes

- The database migrates to schema version 4 (adds `share_token`,
  `share_expires`, `share_writable` to `term_sessions`). The migration is
  idempotent, but **back up before downgrading** to 0.1.3 or older —
  `wsctl backup` first, since the older release does not know about these
  columns.
- New accounts and password changes must be at least 8 characters. Existing
  accounts keep working; `wsctl doctor` reports which ones are below the bar.

[0.1.4]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.4

## [0.1.3] - 2026-09-22

Operations and correctness release: a background lifecycle that needs no
systemd, strict validation of mutually exclusive options, and the last blocking
paths on the event loop removed. No new terminal protocols.

### Added

- **Background lifecycle without systemd**: `wsctl start` / `stop` / `restart` /
  `status` / `logs` / `reload`, plus `wsctl serve --daemon`. The child is
  detached (`start_new_session`), its output goes to a per-port log file, and a
  pid file carries a **process-identity fingerprint** (Linux start time, `ps`
  elsewhere) so a recycled PID is never signalled. `start` refuses to clobber a
  live instance (`start --force` replaces a running one); `status --json` is
  machine-readable; `reload` sends `SIGHUP`, which the server now handles (in
  addition to the mtime watcher).
- **Half-open connection detection**: a WebSocket that has sent nothing for
  `IDLE_TIMEOUT` (120s) is closed with code `4408`. Both clients ping every 25s,
  so a peer that vanished without a FIN is reclaimed instead of lingering.
- **Declarative CLI validation** (`wsctl/cli/validate.py`): options that cannot
  be combined or that depend on each other now fail fast with exit code 2 and an
  actionable message — `--daemon` vs `--reuse-port`, `--ssl-cert`/`--ssl-key`,
  `serve --backend ssh`, `session new --ssh` vs `--backend`, `--ssh-*` without
  `--ssh`, unknown roles, and more. `config set` now type-checks the value
  against the settings model and validates the whole file before writing.
- **One set of user invariants for both surfaces** (`wsctl/core/user_admin.py`):
  the CLI can no longer disable, demote or delete the last admin (which could
  lock the instance out), and `wsctl user passwd` now revokes that user's
  existing logins just like the API does.
- **Consistent backups and restores**: `backup` uses SQLite's online backup API
  (a WAL-mode database copied as a plain file could miss recent commits), and
  `restore` validates the archive, refuses to overwrite without `--force`
  (keeping a `.bak`), and rejects path-traversal members.
- `wsctl connect` reconnects automatically with exponential backoff and
  re-attaches to the same session (server-side replay restores the screen);
  `--no-reconnect` opts out.
- `doctor` now reports the running instance, port availability, `SO_REUSEPORT`
  support and background-start capability.
- Metrics `wsctl_audit_write_errors_total` and `wsctl_maintenance_lag_seconds`.

### Changed

- **WebSocket close codes are now meaningful**: `4400` bad request, `4401`
  unauthenticated, `4403` forbidden, `4404` session not found, `4409` limit
  reached, `4500` server error. The web client stops reconnecting on a permanent
  failure instead of looping, and a missing session no longer pops the login
  dialog at an already-authenticated user.
- Session ids are hex (`token_hex`) so they can never start with `-` and be
  mistaken for a CLI option.
- `--reuse-port` now fails loudly when `SO_REUSEPORT` is unavailable instead of
  silently binding without it (a silent downgrade would defeat zero-downtime
  handovers).

### Fixed

- **Argon2 verification and hashing no longer run on the event loop**: login,
  user creation and password changes run on a worker thread, so a login storm
  cannot stall every terminal.
- `Recorder.close()` (which joins its writer thread) no longer blocks the event
  loop; retention purging, recording-capacity checks and directory listings run
  on worker threads.
- The audit writer survives a failing database write (it logs, counts and keeps
  going instead of dying silently), and concurrent `flush()` calls can no longer
  reorder events.
- `serve --new` with a `tmux` backend that is not installed no longer crashes the
  server at startup.
- The web UI's Esc key closes the topmost modal (previously it could close the
  admin panel behind the QR dialog), the QR dialog is a normal, focus-trapped
  modal, password reset uses a masked input, and assigning a hotkey that is
  already taken is rejected with a warning.

### Testing

- New suites for the validation layer, the pid-file lifecycle, consistent
  backups/restores, and the shared user-admin invariants; WebSocket close-code
  tests; audit-writer failure and ordering tests; a `daemon` end-to-end scenario;
  and browser coverage for the 2FA QR modal.

[0.1.3]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.3

## [0.1.2] - 2026-09-22

Polish and robustness release: disk/DB I/O is moved off the event loop, the web
UI gains an admin surface and a design system, and the CLI gets a few
conveniences. No new terminal protocols.

### Added

- **Admin surface in the web UI**: user management (create, role, enable/disable,
  password reset, TOTP with an inline QR code), an audit log viewer with filters
  and pagination, and recording management (list / replay / download / delete).
- **Terminal search** over the buffer (toolbar button or `Ctrl+Shift+F`).
- **Toasts**, a unified modal component, an inline rename dialog, and a
  confirmation before terminating a session.
- Font-family selection, `Ctrl+Shift+C` / `Ctrl+Shift+V` copy/paste (and `Ctrl+C`
  to copy a selection, right-click to paste), and "follow system" light/dark
  theme.
- Session list enhancements: uptime / idle / buffer size, name filter, and
  "detach all".
- A `wsctl_audit_dropped_total` metric for the async audit queue.
- A **design system** (tokens + components), an icon-based toolbar with a
  mobile overflow menu, theme-gallery colour previews, empty/loading skeleton
  states, a branded login screen, `favicon.svg`, and a PWA manifest.
- README screenshots under `docs/screenshots/`.
- CLI: `config get` / `config validate`, `doctor --json`, `backup`, an inline
  TOTP QR code in `user totp`, and shell completion.
- `contrib/docker/` (Dockerfile + compose).
- `session_sliding_ttl` to keep an active login alive.

### Changed

- **Audit logging is now asynchronous**: events are queued and written in
  batches off the event loop, so `audit_input` and connection events no longer
  add latency to other sessions.
- Recording writes run on a background thread instead of the event loop.
- File uploads are written on a worker thread instead of the event loop.
- `resolve_auth_session` throttles its `last_seen` write (every 30s) instead of
  writing on every request.
- tmux probing is asynchronous and only runs when tmux-backed sessions exist.
- Webhook delivery retries with exponential backoff.

### Fixed

- A client dropped by per-session memory backpressure now has its socket closed
  and can no longer inject input, instead of lingering.
- The audit input line buffer is bounded.
- The last enabled admin can no longer be demoted, disabled or deleted, and an
  admin cannot disable or delete itself (prevents locking the instance out).
- Changing a user's password (or disabling the user) now revokes that user's
  existing login sessions.
- A failed upload no longer leaves a partially written file behind.
- Unified modals close on `Esc` or a backdrop click and trap keyboard focus.
- Enabling TOTP for a user is now audited.

### Testing

- New tests for the async audit writer, admin APIs (users / audit / recordings),
  TOTP endpoints, `last_seen` throttling, and sliding TTL.
- Browser test extended to cover the admin panel, terminal search, the inline
  rename dialog, font selection and the "follow system" theme.

[0.1.2]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.2

## [0.1.1] - 2026-09-22

Hardening release: safe multi-instance operation, bounded resource usage, and a
full Chinese user interface. No new terminal features.

### Added

- **Multi-instance safety.** A per-process lease (`instances` table plus
  `term_sessions.instance_id`) means two servers sharing one data directory
  (e.g. during an `SO_REUSEPORT` handover) never reconcile or reap each other's
  sessions. `--reuse-port` restarts are now correct even with live sessions.
- tmux sessions are namespaced per data directory, so two deployments on the
  same host sharing one tmux server cannot see or kill each other's sessions.
- **Bounded retention.** `audit_retention_days` (default 30) and
  `term_session_retention_days` (default 30) prune the audit log and finished
  session rows; `recordings_retention_days` and `recordings_max_bytes` bound
  recordings on disk.
- `max_sessions_per_user` for per-user session quotas.
- `metrics_require_auth` to require authentication for `GET /metrics`.
- `instance_ttl` controls how long another instance's lease may go unheard.
- `GET /api/sessions/{id}/share` returns the active share token so the UI can
  reuse an existing link instead of silently invalidating it.
- CLI: `wsctl --version`, `wsctl doctor` (environment/preflight checks),
  `wsctl user disable|enable`, `wsctl session rename`. `wsctl config set` now
  rejects unknown keys and suggests the closest match.
- Web UI: a session list (`会话`) to reopen detached sessions or kill them, a
  tab context menu (right-click, or long-press on touch devices), share expiry
  (TTL) selection and link regeneration, and an optional TOTP field on the
  login form.
- `contrib/systemd/wsctl.service` and `contrib/nginx/wsctl.conf` deployment
  examples.

### Changed

- **Closing a tab now detaches (keeps the session running) instead of killing
  it.** This matches the project's core promise that a session is independent
  of its connections. Use the tab context menu, the session list, or
  `Alt+Shift+W` to actually terminate a session; `Alt+W` detaches.
- The entire web UI and CLI help/output are now in Chinese (`lang="zh-CN"`),
  including server API/WebSocket error messages surfaced to the user.
- Live WebSocket terminals now re-validate the login session every few seconds
  and close (`4401`) when the token is revoked/expired or the user is disabled,
  instead of staying connected until the tab is closed.

### Fixed

- **Sessions could leak when a client could not accept the reconnect replay:**
  a session created during the WebSocket handshake is now removed if attaching
  fails, and a client that rejects the replay is no longer left registered in
  the session's client set.
- **Startup race between instances:** `_ensure_admin` no longer crashes with a
  `UNIQUE constraint` error when two instances bootstrap the same database
  concurrently.
- **Schema migration ordering:** the `instance_id` index is created after the
  `ALTER TABLE`, so upgrading an existing 0.1.0 database no longer fails with
  `no such column: instance_id`.
- `RateLimiter` no longer accumulates keys with no recent failures (a slow
  memory leak under credential-stuffing).
- Expired sessions are reaped concurrently instead of one at a time.
- Sessions abandoned by a **crashed** instance are reclaimed/adopted
  immediately (same-host pid check) rather than waiting for the lease TTL, and
  reconciliation now runs on every maintenance tick, so a peer's sessions are
  taken over as soon as it exits instead of only on the next restart.
- Regenerating a share link now asks for confirmation first, so an already
  distributed link is not invalidated by a stray click.
- Multi-instance liveness probing is POSIX-only: `os.kill(pid, 0)` is not a
  liveness check on Windows (CPython maps it to `TerminateProcess`), so the
  lease falls back to the heartbeat TTL there.
- A paused instance whose lease was pruned by a peer re-registers itself on the
  next maintenance tick (idempotent upsert) instead of staying unregistered.
- A command that cannot be started (bad `--command`/`--new`, missing binary) now
  returns a clean `400 无法启动命令` instead of a `500`, and a bad `--new`
  command no longer prevents the server from starting.

### Testing

- New `test_instances.py` (leases, per-instance reconciliation, retention
  purging), plus tests for rate-limiter eviction, recording capacity, per-user
  quotas, share reuse, `metrics_require_auth`, `within_user_quota`, attach
  rollback, and the new CLI commands.
- New backend e2e scenario `multiplex`: two instances share one data directory
  and must not mark each other's sessions stopped.
- Browser test now covers detach-on-close and share-link reuse.

[0.1.1]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.1

## [0.1.0] - 2026-09-21

First release. Single-server web terminal with persistent sessions, multi-user
RBAC, auditing, sharing, a file panel and observability.

### Added

**Sessions & server**
- `SessionManager` with connection-independent PTY sessions, attach/detach,
  scrollback replay on reconnect and bounded output backpressure.
- POSIX PTY backend; optional Windows support via `pywinpty` (`wsctl[win]`).
- Binary WebSocket protocol (raw terminal bytes) with a JSON control channel.
- Multi-tab web UI (vendored xterm.js, zero build step) with auto-reconnect.
- Session renaming, idle/max-lifetime enforcement and metadata reconciliation.
- Per-session client limit (`session_max_clients`), per-session memory hard limit
  (`session_memory_limit`) and per-client byte cap (`client_max_bytes`).
- Per-connection input rate limiting via a token bucket
  (`input_rate_limit` / `input_rate_burst`).

**Backends**
- Optional `tmux` backend (`default_backend`, `--backend tmux`): the shell runs
  inside a tmux session and survives a full wsctl restart, then is reattached
  automatically with the same id and a redrawn screen; graceful shutdown
  preserves tmux sessions (`tmux_preserve_on_shutdown`).
- SSH backend: `backend="ssh"` builds a safe `ssh` argv from a structured target
  (host/user/port/identity/options/remote command) with no shell interpolation.

**Users & security**
- Multi-user accounts with Argon2 hashing and server-side session tokens.
- Role-based access control (admin / user) on the REST API and WebSocket attach.
- TOTP two-factor authentication (`wsctl user totp`).
- Login rate limiting (per IP + username) and a CIDR IP allowlist.
- Origin allowlist on WebSocket handshakes (anti-CSWSH).
- Security response headers (nosniff, frame DENY, referrer policy, CSP).
- Optional submitted-input auditing (`audit_input`).

**Auditing & events**
- Full audit trail with an admin `GET /api/audit` endpoint and `wsctl audit`.
- Optional webhooks (`webhook_url`): every audit event is POSTed as JSON by a
  background dispatcher.

**Sharing**
- Read-only and read-write share tokens (`POST/DELETE /api/sessions/{id}/share`),
  unguessable, optionally time-limited and revocable.
- Anonymous viewers attach with a share link (`?session=&share=`) without an
  account; input is refused for read-only links.
- QR code for a share link (`GET /api/sessions/{id}/qr.svg`) and a share dialog
  in the web UI.

**Recording**
- asciinema cast v2 recording (`core/recording.py`), optionally including input,
  with `auto_record` / `record_input` settings and per-session start/stop.
- In-browser replay via a vendored asciinema player, plus `wsctl session
  record|record-stop|recording`.

**Files & terminal features**
- Web file panel API (list / download / upload) rooted at a configurable
  `file_root`, with traversal-proof resolution and upload size limits.
- Sixel image rendering via `@xterm/addon-image`.
- Opt-in ZMODEM (`sz`/`rz`) file transfer in the browser via `zmodem.js`.

**Observability & operations**
- `/healthz` and Prometheus metrics at `/metrics` (incl. `wsctl_session_bytes`).
- Structured JSON logging (`--log-json`).
- Zero-downtime restarts: `reuse_port` / `--reuse-port` binds with `SO_REUSEPORT`
  so a new instance takes over the port before the old one exits.
- Runtime config hot-reload: file-mtime watcher, `POST /api/config/reload` and
  `wsctl config reload` (restart-only fields are ignored).

**Web UI**
- Dark/light page theme, font size, a terminal theme gallery (10 built-in themes
  plus custom JSON themes) and configurable keyboard shortcuts, persisted locally.
- Responsive/mobile layout adjustments.

**Persistence**
- SQLite storage for users, auth sessions, terminal sessions, audit logs and
  settings; full `term_sessions` columns (`argv`, `env`, `idle_timeout`,
  `max_life`) with an idempotent schema migration.

**CLI**
- `serve` (`--new`, `--backend`, `--reuse-port`), `connect`, `login`, `logout`,
  `session list|new|kill|attach|record|record-stop|recording`,
  `user add|list|del|passwd|role|totp`, `audit`, `config show|path|edit|set|reload`,
  `version`.

**Packaging & CI**
- Hatchling packaging (PyPI: `wsctl`), MIT license, `py.typed`.
- Third-party front-end assets ship with their license texts
  (`src/wsctl/static/vendor/THIRD_PARTY_NOTICES.txt`, `THIRD_PARTY_NOTICES.md`).
- GitHub Actions CI (ruff, mypy, pytest on Python 3.11–3.13), a dedicated
  browser-test job and a backend end-to-end job, plus a release workflow using
  PyPI Trusted Publishing that also creates a GitHub Release.

### Fixed

- tmux-backed sessions now default `TERM` (xterm-256color), so they work in
  headless environments where `TERM` is unset (previously the tmux client
  exited immediately and restart recovery failed).
- A tmux client is never signalled as a process group on shutdown, so the
  freshly forked tmux server (and the preserved session) cannot be killed.
- Revoked/expired share tokens are now enforced promptly: input is rejected
  immediately and the connection is closed, and a periodic re-check detaches
  viewers even on sessions that produce no output.
- Read-only clients can no longer resize the shared terminal at attach time.
- Authorization failures during the WebSocket handshake are delivered as close
  codes (4401), so clients stop reconnecting instead of looping forever.
- File upload opens the destination with `O_NOFOLLOW` (race-free symlink guard).
- Config hot-reload treats an explicitly-empty environment variable as set.
- Web UI: `localStorage.setItem` is guarded, and the record button reflects the
  actual recording state on load and when switching tabs.
- PTY no longer leaks the master fd or the child process when a spawn fails,
  and out-of-range terminal dimensions are clamped instead of raising.
- The PTY write buffer is bounded, so a child that stops reading stdin cannot
  grow server memory without limit.
- Sessions are stopped if post-create setup fails (REST and WebSocket paths),
  instead of leaking a live process with no metadata.
- Disabled users' existing sessions are invalidated.
- Empty commands (400), out-of-range dimensions (422) and duplicate users (409)
  are rejected cleanly instead of erroring out.
- CSP allows `blob:` so the recording player actually loads its cast, and
  `connect-src` was tightened to `'self'`.
- Web UI: guarded `localStorage` parsing, reset the screen on reconnect (no
  duplicated scrollback), share "revoke" targets the session the dialog was
  opened for, read-only viewers can no longer trigger the login modal, and the
  replay player/blob URL is disposed.
- WebSocket teardown now drains queued control messages before closing.
- WebSocket auto-created sessions record `owner_id` and persist metadata.
- `safe_resolve` rejects absolute paths instead of silently re-rooting them.
- The share QR endpoint emits a standalone SVG (with `xmlns`) so it renders
  inside an `<img>`; the previous inline SVG was blank in browsers.
- CSP allows `'wasm-unsafe-eval'` and `worker-src blob:` so the recording player
  can run.

### Testing

- 149 unit/integration tests; concurrency and large-output load tests are opt-in
  (`pytest -m slow`), browser tests are opt-in (`pytest -m browser`), and a
  120-second per-test timeout guards against hangs.
- Browser-level end-to-end tests with Playwright (`wsctl[e2e]`) covering login,
  terminal I/O, multi-tab, hotkeys, file panel, sharing with QR, recording
  replay, theme switching, read-only input blocking and anonymous share links;
  run in CI in a dedicated job.
- `scripts/e2e/run_all.py` runs five backend end-to-end scenarios (server, CLI,
  `connect`, tmux restart recovery, SO_REUSEPORT graceful restart); it runs in
  CI in a dedicated job.

[0.1.0]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.0
