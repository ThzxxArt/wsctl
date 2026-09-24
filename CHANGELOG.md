# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.21] - 2026-09-24

**环形缓冲要留「最新的 N 字节」，不是「整块整块地扔到不超容为止」。**

CI 的 browser 腿挂了一条：重连后等不到 `XXXX`。根因不在 0.1.20 的改动，在
0.1.17 就写下的 `_trim`——一场**PTY 读边界彩票**：

```
scrollback：[按键回显…, X洪峰块(5000B), 提示符(50B)]，预算 512B
旧逻辑：while size>512: 整块弹出  →  只超 50 字节，却扔掉整个 5000B 的 X 块
结果：只剩 b'prompt> '（8B）→ 重连回放无内容 → 超时
```

同一字节流三种分块方式（纯 Python 追踪）：X 洪峰与提示符**分两次读**（慢 CI
的典型边界）必挂；**合一次读**（快机把两次写合并进同一次 `read()`）必过；
X 分两读、尾巴随提示符则必过。0.1.18/0.1.19 的 CI 绿与本地全绿都是边界
运气，0.1.20 抽到了坏的那次——**产品不允许把命运押在内核怎么分片上**。

### Fixed

- **`Scrollback._trim` 整块淘汰只在「移除整块仍装得下预算」时进行**，余量
  交给尾切路径（保留最新 N 字节的本义）。对齐规则不变：淘汰到「最后一块
  结束在序列外」；唯一保留块从序列中间开始时跳到边界。

### 守卫

- **三布局对拍**：同一字节流按三种 PTY 读边界分块，必须留**同一尾巴**——
  从此与内核分片无关。上一版宣称的「丢帧触发确定性」只钉了丢帧那一半，
  scrollback 留什么这一半仍是彩票，这次一并钉死。

### Verified

464 unit（含 29 前端结构契约 + 读边界不变性）· 30 browser · 6 slow ·
10 e2e 全绿；ruff / mypy strict / `node --check` 通过。

## [0.1.20] - 2026-09-24

**花屏的根因不在字节流，在恢复模型：屏幕在应用内存里，回放字节流永远还原不了 TUI。**

用户现场：网页终端里跑 opencode 等发布，直接花屏，「怎么弄都恢复不了」，只能
杀会话。此前 0.1.12–0.1.17 修的丢帧切点、`_trim` 丢帧、回放被绞碎——都是真
bug，但它们只把字节流里的**洞**变少。死局是模型性的：

```
回放 = 重放「原始字节流」        TUI 的屏幕 = 存在「应用内存」里
字节流有洞（丢帧）─────────┐
字节流无头（scrollback evict）─┤
                             ▼
    TUI 增量绘制（移光标→覆写）落在错误的底子上 → 永久花屏
                             │
    resync = 重放同一段有洞/无头的字节流
                             ▼
              复现同一片花屏 → 只能杀会话
```

**产品自己的文案早就写着答案**：失步提示条让「调整一次窗口大小触发自身重绘」
——真正的恢复机制是**让应用自己重绘**（SIGWINCH 真实尺寸变化），却让用户手动
做；而它的另一句建议 Ctrl+L 对 TUI 根本是错的（opencode 捕获该键）。

### Fixed

- **`TermSession.nudge_repaint()`**：`rows-1` → 恢复，**两个真实尺寸变化**
  （Linux 仅在尺寸改变时投递 SIGWINCH）。每个 TUI（vim / htop / opencode-ink …）
  在 SIGWINCH 上从**自己的模型**全量重绘——本地终端花屏 vim 的教科书解法。
- **attach 回放后自动触发**（有回放才触发；全新会话无需）；**resync 回放后
  无条件触发**（回放 + 重绘双保险）。服务端发起：只读分享观众与 CLI 客户端
  同样受益——**刷新显示不是终端输入**。恢复的目标尺寸取会话**当前**尺寸，
  50ms 窗口内用户真实改大小不会被退回。
- **注定放不下的帧不得拉整个队列陪葬**（同族漏网，被上者的时序暴露）：
  `WsClient.put` 对超预算帧先 `_shed_for` 逐出**全部**已排队帧（含刚回放的
  内容）腾地方，然后该帧因永远放不下再被丢——队列空了、帧也没活，屏幕什么
  都没有。现为**单独丢帧、不动队列**（1KiB 预算 + 64KiB PTY 读的确定性用例
  钉死：`REPLAY` 必须活过 `64KiB X`）。
- 失步提示条改为真实表述（「同步后全屏程序会自动重绘」），删除 Ctrl+L 的
  错误建议。

### 与前几版的关系（诚实边界）

丢帧切点、`_trim`、回放合批那些修复**没有白做**——它们减少字节流的洞，也就是
减少花屏的**发生**。但洞无法为零（背压丢帧是设计行为），且 4MB scrollback 必然
evict 长会话的开头，所以**发生后的死局**必须靠应用重绘解。本版补的就是这半边：
花屏仍然可能发生，但**一定可恢复**。

### Verified

463 unit（含 28 前端结构契约）· 30 browser · 6 slow · 10 e2e 全绿；
ruff / mypy strict / `node --check` 通过。判别性守卫：**16 字节 scrollback +
vi + 重连**——回放只剩首绘残片。门槛的算术是实的：画 *n* 个 `~` 行的最紧凑
绘制是每行 `~\n`（2n−1 字节），10 行 ≥ 19 字节，**16 字节片段在任何绘制顺序下
都不可能伪造**；无应用重绘则 `~` 行为零（突变体已证：摘掉 nudge 必红超时），
有重绘则回归十行以上。单测钉「两次真实尺寸变化 + 尺寸恢复」与「超预算帧
不得清空队列」；契约钉「nudge 不在 `writable` 门内（只读观众同享）」。

## [0.1.19] - 2026-09-24

**2FA 二维码从「45px 邮票」修成正常大小：根因是 SVG 缺 viewBox。**

分享会话的二维码一直是正常大小，管理里启用 2FA 弹框中的码却**非常小**——
两者 CSS 盒子同为 200px，但根本不是同一个东西：

- **分享码**走 `<img src="qr.svg">`：替换元素会把**整份 SVG 文档**缩放到盒子，
  segno 的 `width="45" height="45"` 无关紧要；
- **2FA 码**是 `innerHTML` 注入的**行内** `<svg>`：segno 默认输出**没有 viewBox**，
  CSS 的 `width/height: 100%` 只放大视口、用户单位仍是 1:1 像素——码永远按
  固有的 **45px** 画在 200px 白盒的左上角。

`qr_svg()` 现按固有尺寸补 `viewBox`（`0 0 W H`），行内 SVG 的内容才真正随容器
缩放。尺寸同时对齐分享码规格取 **220×220**（分享 200×200；两者是同一量级的
兄弟，且 2FA 必须一次扫对）。

### Fixed

- **`qr_svg` 缺 viewBox**（行内使用不缩放，45px 邮票的真正根因）。`<img>` 路径
  一直是好的，故只有 2FA 中招——两条路径的差异正是定位线索。

### Changed

- `.qr-holder` 与 `.qr`（分享码）同规格：220×220 对 200×200。

### 守卫（本版的教训收录）

守卫连踩两个坑后才钉死：① 量 `svg` **元素盒子**（被 CSS 撑到 184px）——45px
的码照过不误；② 量路径墨迹却把阈值设成 200px——墨迹只占 viewBox 的 82%
（静区不画路径），修复后的 183px 反被误杀。现双钉：**DOM 上的 viewBox 属性**
（根因本体）+ 路径墨迹 ≥ 150px（与缺陷态 ~37px 隔 4 倍），单测再断言
`viewBox` 与固有宽高一致。**量盒子不等于量内容**。

### Verified

459 unit（含 28 前端结构契约）· 29 browser · 6 slow · 10 e2e 全绿；
ruff / mypy strict / `node --check` 通过。

## [0.1.18] - 2026-09-24

**连接生命周期收口：重连不双开、回放不掺旧帧、状态随事实复位。**

（版本说明：本内容曾以 0.1.17 发出后即撤回——PyPI 的文件名一经使用永久不可复用，
被删除的 0.1.17 无法原号重发，故以 0.1.18 交付。内容与 0.1.17 相同，并含撤回后
立即根治的 CJK 宽度用例三缺陷，见文末 Verified。）

0.1.12–0.1.16 根治了卡屏 / 闪屏 / 花屏的主根因。这一版不引入新能力，只把
`断开 → 重连 → 回放 → 失步` 这条环路上最后一批**「动作做了、声明没跟上」**的
缺口封掉——六处可靠性缺陷、一处花屏、三处流畅性、四处可用性。

### 花屏：scrollback 回放起点（本版最重的一条）

- **`Scrollback._trim` 不再丢掉「以转义序列半截结尾」的队首块。** 原实现的
  「重新锚定回放起点」第二段循环**不以超容为前提**、每次 `append` 都跑，判据还
  用的是**新队首自己的结束态**（而正确判据是**被淘汰前缀的结束态**，即
  `WsClient._drop_oldest_binary` 的 `if not ends_inside` 规则）。后果：PTY 一次
  读恰好把 `ESC[3` / `1m` 切在两帧时，**前半帧在零内存压力下就从 scrollback 消失**，
  回放以 `1m` 开头——正是 T5 强形式点名的「`1mABC` 不以 ESC 开头、弱检查照过」
  的花屏。现在淘汰只在超容时进行，且丢到「最后一块**结束在序列外**」为止；
  `_inside` 从 `bool` 升级为**状态串**，因为「保留的最后一块从序列中间开始」时
  只有知道序列种类才能跳到它的终点（`AnsiTracker.resume` / `skip_to_boundary`）。
- **单块超容尾切真正对齐序列边界。** `_align_ansi` 是**空转**（`return buf`），
  尾切可落在 `ESC[31m` 中间；`ansi.skip_partial_prefix` 是现成的正确实现却全仓库
  零调用。现在尾切用 `AnsiTracker` 喂被淘汰前缀判定，落在序列内则向后丢到边界。
- **守卫从弱形式升级为强形式。** `test_replay_start_is_re_anchored_after_eviction`
  此前只断言「头块自己结束在序列外」——按上述现实行为头块是 `1mVISIBLE...`，
  **用例照样绿**。这正是 0.1.15 写下的教训（「能让错代码通过的守卫不是守卫」）
  在 scrollback 侧的复发。现在与 T5 同款强形式：把**被淘汰前缀**喂给 tracker、
  断言 cut 点 outside，并新增**零压力跨帧序列必须完整保留**回归（修复前必红）。

### 可靠性：连接状态机六缺口（同一条根：状态没有随事实复位）

- **重连回放不再被帧上限绞碎（用户现场缺陷）。** 一位用户在云服务器上跑完 TUI、
  退回 shell 闲置后，界面**自动**提示「失去同步」，并把**已经退出**的 TUI 又画了
  回来——不完整、花屏。两条链路叠加：① 即上文的 `_trim` 回放起点缺陷（TUI 的
  `ESC[?1049l` 退出备用屏被打断 → 终端卡在备用屏，TUI「复活」；颜色序列缺前半 →
  花屏）；② **`attach` 把整个 scrollback 一次性同步入队**，而 TUI 的输出是数千个
  微小 PTY 读——出站队列的**帧数上限**（512）远早于字节预算（8 MiB）被触发，
  回放**自己**把自己的头丢掉，再补一发 `desync`。人坐在空提示符前，却被告知
  「输出过快」。现在回放帧按 64 KiB 合批（数千小帧 → 数十帧，字节流逐位不变），
  `attached` / `resynced` 都携带 `incomplete`：**回放自己丢了帧就不许宣布成功**。
  回归用例 `test_a_tui_sized_replay_must_not_be_shredded_by_the_frame_cap`
  同一缓冲双客户端对拍——合批路径零丢弃，原始小帧路径**必须**丢弃（否则守卫
  分不清修复与缺陷）。
- **重连不再双开（孤儿 WebSocket）。** `scheduleReconnect` 与 `ensureConnected`
  各自调 `connect`，互不清对方的退避定时器，`onclose` 还把已关闭的 socket 留在
  `s.ws` 里。掉线后点一下标签 = 手动 `connect` + 定时器 `connect` 各建一条，
  前者被 `s.ws = ws` 覆盖后成为**孤儿**——服务端仍挂在 `attached[]` 上，且它的
  处理器仍在写同一个 `s.term`。现在每个 `connect` 都从 `resetTransients` 起步
  （清退避 / 写队列 / resync 锁），替换前先关旧 socket，且**所有 ws 处理器带代际
  比对**（`s.ws === ws`），过期回调一律 no-op。
- **挂起/重连后旧 `writeQueue` 帧不再掺进新回放。** 断线前 16ms 合批窗口里残留的
  帧，会在 `term.reset()` + 回放之后被 `flushWrite` 合并写入——屏幕从脏处开始画。
  现在写队列是连接级瞬态，新连接必清。
- **resync 进行中遇断线重连，回放不再被丢弃。** `enqueueWrite` 在 `resyncing`
  期间丢帧（防重复绘制）是**对的**，但重连后的 attach 回放是 resync 的**超集**，
  照样被丢——用户面对空白屏幕直到 4s 超时。0.1.15 修过「重同步失败白屏无提示」，
  这是同一族的漏网。现在 `onopen` / `attached` 无条件抬起 resync 写锁。
- **失步徽标在「重连 + 完整回放」后复位。** 客户端丢帧只丢**投递队列**，服务端
  scrollback 是完整的；重连回放本就修复屏幕，但失步条还在说「内容已省略」并继续
  提供「重新同步」——**状态在说谎**。现在 `attached` 在本连接未见过 `desync` 且
  服务端未报 `incomplete` 时收起徽标；否则保留并写明「会话回放未完整」。
- **`resynced` 不再对「resync 自己丢掉的帧」宣布成功。** 大缓冲的 resync 回放
  同样可能触发丢弃，而它丢的是**正在重建的屏幕的开头**。服务端现在对比回放前后的
  `dropped_events`，在 `resynced` 里带上 `incomplete`；前端据此保留徽标并写明
  「重新同步未完整」。诚实边界与失步条同一口径。
- **`evicted` 不再把标签打成 standby。** 4410 / 内存驱逐是**可恢复**的，`onclose`
  紧接着就会自动重连；中间态显示「未连接」与下一行代码直接矛盾。Toast 是消息，
  连接指示灯从此由 `onclose` / `describeConnection` 唯一驱动。
- **后台标签不再改写状态栏（回归 review 发现，本版引入后当场根治）。** `setConnectionFor`
  只守住了文字，没守住「点击立即重连」的 title/cursor——后台标签一进入退避就把
  活动标签的指示灯抢过来，还指向错误的会话。现在 `paintConnectionFor` 是唯一写入者，
  显式状态会顺手清掉重试附属物；`connect` 里绘制挪到 `s.ws` 赋值之后（原先会闪一帧
  「未连接」）。契约 `test_a_background_tab_may_never_rewrite_the_connection_indicator`
  钉死「全文件只有两个合法写入者且都带守卫」。

### 流畅性

- **终端搜索与其它过滤框同款 debounce（250ms）。** 「全部命中高亮」每次按键都是
  一次全缓冲扫描加装饰重建；文件/会话筛选早就 debounce 了，搜索没有——大输出
  会话里打字会一顿一顿。Enter 仍然立即搜索。
- **单次 `term.write` 封顶 256 KiB。** 4 MiB 回放整包合批是一次数 MiB 的主线程
  解析，恰好发生在重建屏幕时。超出部分留到下一帧，字节序不变。
- **大段粘贴按 32 KiB 分片发送。** 整包一个二进制帧会在**单次调用**里占满 PTY
  写缓冲（其上限只在入口判断），此后每次按键都被当成「输入过快」丢弃——键盘
  看起来死了。WS 保序，分片到达仍是同一字节流；超过 32 KiB 时提示「已分片发送」。

### 可用性

- **重连过程可见、可打断。** 退避中状态栏显示「重连中（Ns 后重试）」，**点击立即
  重连**。此前只有「已断开」三个字，分不清「正在重试」和「彻底死了」。
- **预览/编辑关闭前确认未保存的修改。** 遮罩 / Esc / 取消三条路径原本直接清空
  编辑器；误触遮罩丢一整份改动。现在与已加载内容比对，脏才问（`warn` 档）。
- **`Alt+←/→` 与浏览器保留集的自相矛盾已按实测定案。** 这对组合键**既是**默认的
  上一/下一标签，**又**登记在 `BROWSER_RESERVED`（「浏览器占用、绑了也没用」），
  而且保留集里的箭头条目是混合大小写、查找却统一 `toLowerCase()`——**警告从来没
  触发过**。实测：页面收得到这对 keydown，`preventDefault()` 可取消浏览器历史
  导航（`test_browser_alt_arrow_really_switches_tabs` 是证据）。定案：从保留集
  移除、保留默认绑定；保留集全部小写化；帮助浮层的 ✅/⚠️/❌ 与保留集、
  `CHORD_RELIABILITY` 合并为**单一事实源** `chordGrade()`。
- **文件面板刷新按钮只绑一次。** 原本重复注册，点一次发两次请求、状态行闪两次。

### Verified

457 unit（含 26 前端结构契约）· 29 browser · 6 slow（T1–T5）· 10 e2e 全绿；
ruff / mypy strict / `node --check` 通过。**10 项突变体反向验证全部证毕**
（逐项注入后对应用例必红，再逐项恢复）：代际守卫 · onopen 抬 resync 写锁 ·
attached 清失步徽标 · `_trim` 零压力丢块 · 写队列连接级清理 · 搜索 debounce ·
保留集小写化且不含默认键 · `evicted` 不再 standby · `resynced` 相信 `incomplete` ·
**回放帧合批（原始小帧路径必须被对拍用例证伪）**。

发布前终检（第三轮 review）又修四处：**`__version__` 与 `pyproject.toml` 版本号
分叉**（包仍报 0.1.16，`wsctl --version` / `/healthz` 读的都是它——发布面直接
说错版本；旧用例只断言输出里有 "wsctl" 这个词，任何版本都能过，已改为断言
**两个事实源一致且命令打印声明值**）；**回放合批帧宽必须服从客户端字节预算**
（0.1.17 自引入的回退：固定 64 KiB 合批帧在 `client_max_bytes < 64 KiB` 时
**整帧放不进预算被整段丢弃**，比合批前的小帧更糟——现按 `replay_target_for`
取 `min(64 KiB, max_bytes)`，并补「帧不超预算 / 容得下就完整 / 容不下如实丢」
三分契约）；CHANGELOG 声明的「单次 `term.write` 封顶 256 KiB」与「点击立即
重连」**此前无守卫**，均补结构契约；两条 browser 用例的丢帧触发从「洪峰竞速」
改为「单次大写入 vs 小预算」的确定性触发（`seq 1 40000` 只有在 PTY 读恰好
跑赢消费者时才丢帧——全量套件下正是这种「快机器上才绿」的等待）。

撤回后追加根治（CJK 宽度用例三缺陷）：① **回显假绿**——命令行自带
`中文测试|`/`abcdabcd|`，等待匹配输入、两列量的是回显行同一个 `|`，对齐断言
在 shell 未打印时即「通过」（T1 回显陷阱复发）；② **`printf` 八进制转义依赖
shell 文法**，CI 上狐狸行根本没产出——现输入纯 ASCII、Python 运行时造字符；
③ **完成标记算错**：`$((696*606))=421776` 却在等 `421236`。三行现均结构性
防伪：CJK/狐狸在输入里只是 `\u` 字面量、ASCII 行运行时 `'abcd'+'abcd'` 拼接、
完成标记用套件既定 `$((700*700))=490000`。

> scrollback 修复前后的差分本身就是花屏字节级证据：突变体注入旧判据后，
> `ok \x1b[3` + `1mRED\x1b[0m` 的快照变成 `1mRED\x1b[0m`——颜色序列的前半没了，
> `1m` 成了文字。强形式用例把这一点钉成永久回归。
>
> 全量回归 review（两轮）又挖出**本版自己引入 / 同族漏网 / 既有弱守卫**共八处并
> 当场根治：后台标签抢写状态栏、`connect` 绘制早于 `s.ws` 赋值闪「未连接」、
> `flushWrite` 直接调用留孤儿定时器（后续写入被卡 16ms+）、`requestResync` 清队
> 后断管、`chordGrade` 给空绑定误评 ⚠️、**CLI `wsctl connect` 把
> `desync`/`notice`/`evicted`/`incomplete` 全部静默吞掉**（浏览器有失步条、CLI
> 一声不吭——「系统知道、用户看不见」在第二客户端上复发）、0.1.15 遗留的 resync
> 用例弱等待（`length > 50` 被空白行骗过、`count("40000") <= 2` 允许零命中——
> 空屏全过）、本版 resync 中断用例同款弱等待。状态栏写入者数量、`flushWrite`
> 的断管义务、CLI 通知可达性、回放内容非空的**实质**等待，均已升级为结构契约或
> 行为用例；`AnsiTracker.resume`/`skip_to_boundary` 补直接单测。

## [0.1.16] - 2026-09-24

**四个工作弹框按内容重新定宽。**

它们当初是按**字段数量**定尺寸的，而不是按**字段里装的是什么**：新建会话有两列的
SSH 块、会话列表每行是「名称 + 元信息 + 两个动作」、管理面板铺一张**六列**审计表、
设置里跑两列快捷键网格。四者都在 360–560px 的盒子里把本该一行放下的东西折了行。

### Changed

- **宽度阶梯重定**：`sm 400 / md 560 / lg 720 / xl 920`（此前 `380 / 480 / 560 / 720`）。
  阶梯必须单调——两档宽度一旦塌成一档，弹框之间就再也没有宽度上的区分度。
- **新建会话** 360 → **720**（`lg`）：SSH 的用户名/端口两列终于并排，不再被挤成两行。
- **会话列表** 560 → **720**（`lg`）：名称、元信息与「打开 / 终止」同排。
- **管理** 560 → **920**（`xl`）：审计表六列（时间/事件/用户/会话/IP/详情）不再换行。
- **设置** 560 → **720**（`lg`）：快捷键两列网格的标签与输入框不再互相挤压。
- 顺带删掉 `settings-card` 这个只用来覆盖宽度的类——宽度该由尺寸档位表达，
  一个弹框私有的宽度值就是「阶梯被绕过」的入口。

### Added

- **宽度回归守卫**（`test_working_dialogs_are_wide_enough_for_their_own_content`）：
  四个弹框各自的档位与该档位的最小宽度一并钉死，含「四档必须单调」断言。
  将来谁「顺手调小一个数字」都会直接红。

### Verified

431 unit（含新增契约）· 24 browser · 6 slow · 10 前端结构契约 · 10 e2e 全绿；
ruff / mypy strict / `node --check` 通过。

## [0.1.15] - 2026-09-23

**根治网页终端的卡屏 / 闪屏 / 花屏，并修掉四处界面缺陷。**

### 卡屏 / 闪屏 / 花屏（同一条根：纯 DOM 渲染 + 每帧一写 + 无 CJK 宽度表）

- **渲染三件套**：`@xterm/addon-webgl`（主）→ `@xterm/addon-canvas`（无 GPU 兜底）→
  xterm 的 DOM 渲染器，三级探测、逐级降级，**加速失败绝不留白屏**。
  此前是纯 DOM 渲染，一次 `seq 1 50000` 就是数千次解析-比对-重绘，主线程饱和即卡死。
  设置里新增**渲染器实况**（`当前：WebGL · Unicode 11`）——WebGL 在部分机器上会静默降级，
  「我到底在用哪个」是任何性能反馈的第一问，不该靠猜。
- **`@xterm/addon-unicode11` + CJK 等宽字体栈。** xterm 自带的是 **Unicode v6** 宽度表，
  东亚字符宽度早已过时：每个中日韩字符占错格数，整行横向漂移——中文场景下这就是「花屏」。
  字体栈同时补上 `Sarasa Mono SC` / `Noto Sans Mono CJK SC` / `Microsoft YaHei Mono`
  （CJK 前进宽度恰为拉丁的两倍）。**宽度表与字体是同一修的两半，缺一不可。**
- **前端帧合批**：`onmessage` 不再每条消息一次 `term.write`，改为入队后**每动画帧合并一次**。
  N 次解析变 1 次。合批次数经心跳回传为 `wsctl_ws_frames_coalesced_total`，线上可观测。
- **尺寸合流**：`window.resize` 与 `term.onResize` 双向 debounce 120ms，且**尺寸未变不发**。
  拖动窗口原本每像素一次 `resize`，远端每条一次 SIGWINCH 与全屏重绘——闪屏与卡屏同时来自这里。
  `applyPrefs` 也只重排**当前活动**会话，其余标脏、切到时再排。
- **服务端丢帧 O(1)**：出站队列拆成「字节帧 + 控制帧」双 deque，各自带单调序号，
  `run()` 按序号归并出队（顺序与单队列完全一致，由用例钉住）。淘汰最旧字节帧从
  **线性扫描 O(n)**（一次突发 O(n²)）变成 `popleft()`。那道扫描跑在 `_broadcast`
  **持锁期间**，也就是正在读 PTY 的那条事件循环上——大输出时循环卡住、内核缓冲涨满、
  **shell 自己停止输出**。这是**服务端**卡屏，与 0.1.12 修的「客户端卡屏」是两条链路。
  同时把投递移出锁外（快照在锁内取，顺序保证不变）。

### 花屏三修

- **ZMODEM 传输中禁用开关。** 原本传输中可卸载 sentry，协议字节随即直写 `term.write`
  ——**把 ZMODEM 握手当文本渲染，真正的乱码**；同时输入锁被释放，按键打进协议流。
  现在传输中按钮与菜单项一并禁用并说明原因，卸载只可能发生在 `session_end` / `on_retract` 之后。
- **失步徽标 + 一键重新同步。** 丢帧保住了连接，但全屏程序的屏幕是用「有缺口的流」画出来的，
  下次局部重绘就会画错格。服务端丢帧时下发 `desync`，界面常驻提示并提供**「重新同步」**
  （清屏后请求服务端重放它仍持有的全部内容）。**诚实边界**：服务端也丢掉的字节无法找回，
  全屏程序仍可能需要它自己的重绘（Ctrl+L 或调一次窗口大小）——提示条原文写明，不假称完全恢复。
- **标签切换不再闪一帧空白。** 原来 `fit()` 推迟到 `requestAnimationFrame`，浏览器先画了
  切换、而终端还是隐藏时的零尺寸。现在**同步** `fit()` + `refresh()`，在那次绘制之前完成度量。

### 界面四缺陷

- **Toast 会自己关了。** 「错误常驻直到用户关」落地成了永久堆积——一次批量操作十几条
  error 盖住终端。现在全部按严重度定时消失（成功 2.5s / 提示 4s / 警告 6s / **错误 10s**），
  **悬停暂停计时**（长文可以慢慢读），**最多同时 4 条**、其余折叠为「还有 N 条」，
  **同类消息合并计数**（`×3`）而不是叠三张卡。
- **弹框设计系统 v2**：语义化尺寸（`sm/md/lg/xl`）、**按钮三档**（`primary` 默认操作 /
  `ghost` 次要 / `warn-btn` 将失效或覆盖 / `danger` **仅限不可逆**）。此前所有确认都是红的
  ——连「生成新链接」也是，红色于是不再意味着「这会毁掉什么」。字段分组卡片 + 两列表单网格 +
  统一焦点环。
- **管理弹框「选中概览、内容是用户」**：`index.html` 标了概览为 `active`，而 `adminTab`
  还是 `"users"`——**两个事实源**。现在 `adminTab` 是唯一事实源，按钮高亮由它派生，
  且选择器收窄到 `#admin-overlay .tab2`（裸 `.tab2` 还会命中会话弹窗的「运行中/已结束」，
  顺手把人家的高亮也清了——**连带 bug 一并根治**）。
- **设置弹框加宽到 560px 并分组**：外观 / 终端 / 交互 / 快捷键四张卡片，快捷键改两列网格。
  此前 360px 里塞主题画廊 + 15 条快捷键，是「套娃滚动条 + 被挤扁的标签」。

### 大数据量：可验收数值（`pytest -m slow`）

| 门 | 目标 | 实测 |
|---|---|---|
| T1 `seq 1 200000` 端到端 | ≤ 8s 且连接不掉、帧不丢 | 0.10s |
| T2 8 会话并发 `seq 1 50000` | ≤ 20s 且**零**驱逐/背压丢弃 | 0.16s |
| T3 该洪峰下事件循环延迟 | < 50ms | 通过 |

> T1/T2/T3 的**完成标记用算术展开**（`$((700*700))`）而不是字面量：字面量会命中终端
> **回显的命令行**，等待立刻返回、断言在 50ms 内「验证了 20 万行」。这是 0.1.11 在 e2e
> 里踩过并写进 CHANGELOG 的同一个坑，我自己又踩了一次，已在测试里写明原因。

### 观测

- `wsctl_event_loop_lag_seconds` / `..._peak_seconds`：0.5s 看门狗自报调度延迟
  （「卡住」是因为循环被堵还是只是忙，此前无法区分）。
- `wsctl_ws_frames_coalesced_total`：浏览器侧合批省下的写入次数，经心跳回传。
- `wsctl_shed_resync_requests_total`：失步后请求重新同步的次数。
- `/api/overview` 的关键指标白名单补入 shed / lag / resync。

### 三轮全量回归 review 又挖出的三个产品缺陷（均已根治）

- **待命标签被激活时完全不可见。** `.term-pane` 基态是 `display:none`，可见性由
  `.term-pane.mounted.active` 决定，而 `mounted` 只在 socket `onopen` 时加上——
  于是一个标签在「已激活但 socket 尚未打开」的窗口里**既不显示也无尺寸**。现在
  `activateTab` 先挂载再测量。
- **挂起标签可能把用户正在看的屏幕清空。** `suspendTab` 无条件摘掉 `mounted`；
  若被挂起的恰好是当前活动标签，`.active` 失去 `mounted` 后回到 `display:none`。
  现在只卸载**非活动**标签。
- **重同步失败后是一块白屏且无任何提示。** `requestResync` 一开始就收起失步条，
  而回放一旦没到，用户面对的是空白屏幕、没有说明、也没有可再点的按钮。现在提示条
  **留到 `resynced` 确认成功才收起**，超时则以「重新同步未完成」重新出现；
  重连点也做了防抖，不会在回放中途又清一次屏。

### Verified

431 unit · 24 browser · 6 slow（T1–T5）· 9 前端结构契约 · 10 e2e 全绿；
ruff / mypy strict / `node --check` 通过。**13 项突变体反向验证全部证毕**
（每项注入后对应用例必红，逐项隔离执行）：管理页签单源 · 错误 Toast 会消失 ·
确认按钮分档 · 禁用 Unicode11 · 丢帧不重对齐边界 · 静默丢帧必报告 ·
失步必须是 `desync` · `resync-begin` 起点标记 · 重同步成功才收提示条 ·
挂起不得白屏活动标签 · 无变化不重测 · 传输中禁用 ZMODEM 开关 · 挂载保留布局盒。

> 三轮 review 里**加强了三处「看起来在测、其实测不到」的守卫**，都是突变体逼出来的：
> ①「丢帧对齐 ANSI 边界」原先只断言 `is_boundary_aligned(kept)`，而 `1mABC`
> 这种**序列尾巴**并不以 ESC 开头、照过不误；②CJK 宽度用例原先量 `中`——它在
> Unicode v6 与 v11 里**同为宽字符**，改用 Unicode 6 之后才有的 🦊 才真正判别；
> ③「挂载面板保留布局盒」原先查整份 CSS 文本，而**这条规则的注释里恰好写着**
> `visibility: hidden`——改成只查规则体。**能让错代码通过的守卫不是守卫。**
>
> 浏览器侧读屏原先依赖 `.xterm-rows` 的 `innerText`，而 **WebGL/Canvas 渲染器
> 根本不产生 DOM 行**——换渲染器等于换掉全部断言。现以 `window.__wsctlScreen()`
> 与 `window.__wsctlRowCells()`（读 xterm 自己的缓冲）为准，渲染器无关，
> 支持诊断也需要它们。

## [0.1.14] - 2026-09-23

**「操作无声无息不执行」——对话框的静默取消改为串行排队。**

0.1.13 发布后 CI 暴露的第三个真缺陷。`test_browser_flow` 的重命名一步报超时，
但它的自诊断输出证明**根本不是超时**：

```
labels=  [{'label':'bash'}, {'label':'bash'}, {'label':'bash'}]
sessions=[{'name':'bash'}, {'name':'bash'}, {'name':'bash'}]
```

服务端三个会话仍叫 `bash`——**PATCH 压根没发出去**。

### Fixed

- **`promptDialog` / `confirmByName` / `confirmDialog` 改为串行排队。** 三者共用一个
  互斥标志，本意是防止第二个弹窗覆盖你正在输入的内容（那确实出过事：一次重命名把
  *旧*名字发回了服务端）。但护栏的实现是**对第二个调用返回 `Promise.resolve(null)`**，
  而所有调用方都把 `null` 读作「用户取消」——于是操作**悄无声息地没执行**，任何地方
  都没有说明。**静默丢失比覆盖输入更糟。** 现在第二个弹窗排队等第一个跑完再真跑。
- **`test_browser_flow` 的等待预算统一为 60 秒。** 此前是 5s/10s/15s/30s 混杂，冷启动
  的 CI runner 上某一环耗尽预算就会红，而本地全绿。现在一处常量统一管，慢机器只是
  更耐心，不会更倒霉。

### Verified

browser 17/17（含重命名一步）· 420 unit · 10 e2e 全绿；`node --check` 与 ruff/mypy
strict 通过。修改过程中曾把 `app.js` 改成语法错，被 `node --check` 当场抓住并恢复到
已提交良态后整块重做——写在案，避免重犯。

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
