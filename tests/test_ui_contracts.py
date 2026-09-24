"""Structural contracts in the front-end sources.

``tests/test_closecodes.py`` established the pattern: the browser cannot import
Python, so the two halves of a shared contract are pinned by parsing the
source. The rules below are the same idea for the 0.1.15 fixes -- each one
guards a defect that shipped because a *behaviour* and its *declared intent*
drifted apart, and each is checked as a fact about the code rather than a
feeling about the UI.

They are cheap and run in the default suite; the browser tests then verify the
behaviour end to end.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "src" / "wsctl" / "static" / "app.js"
INDEX = ROOT / "src" / "wsctl" / "static" / "index.html"
APP_CSS = ROOT / "src" / "wsctl" / "static" / "app.css"


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_admin_panel_has_one_source_of_truth() -> None:
    """The highlight and the content must come from the same variable.

    ``index.html`` marked 概览 as selected while ``adminTab`` said ``"users"``,
    so the panel opened with one tab lit and another tab's content underneath.
    There is now exactly one writer, and its default matches the markup.
    """
    js = _js()
    html = INDEX.read_text(encoding="utf-8")
    assert 'let adminTab = "overview";' in js, "default must be the overview tab"
    assert re.search(r'data-tab="overview"', html), "markup must offer the overview tab"
    # ``setAdminTab`` is the only place allowed to assign to ``adminTab``.
    # ``(?!=)`` matters: ``adminTab === "overview"`` also starts with
    # ``adminTab =`` and was counted as a write.
    assignments = re.findall(r"adminTab\s*=(?!=)", js)
    assert len(assignments) == 2, (
        "adminTab must be assigned exactly twice (declaration + setAdminTab), "
        f"found {len(assignments)}: {assignments}"
    )
    assert "function setAdminTab(" in js
    # And the tab buttons are scoped: a bare ``.tab2`` also matches the session
    # dialog's 运行中/已结束 tabs and used to clear their highlight too.
    assert '"#admin-overlay .tab2"' in js or "'#admin-overlay .tab2'" in js


def test_every_toast_kind_auto_dismisses() -> None:
    """No toast may be permanent -- including errors.

    Errors used to stay until clicked ("so a failure is not swallowed by a
    timer") and one batch operation pinned a dozen of them on screen forever.
    The lifetime table must cover every severity with a non-zero timeout.
    """
    js = _js()
    match = re.search(r"const TOAST_MS = \{([^}]*)\}", js)
    assert match is not None, "TOAST_MS table not found"
    pairs = dict(re.findall(r"(\w+)\s*:\s*(\d+)", match.group(1)))
    for kind in ("ok", "info", "warn", "error"):
        assert kind in pairs, f"toasts of kind {kind!r} have no lifetime"
        assert int(pairs[kind]) > 0, f"{kind} toasts never auto-dismiss"
    # And the stack must fold rather than cover the terminal.
    assert "const TOAST_MAX" in js
    assert "toastUnfold" in js


def test_confirm_button_weight_tracks_the_risk() -> None:
    """``danger`` is for irreversible loss only.

    Every confirmation used to be red, including regenerating a share link, so
    the colour stopped meaning "this destroys something".
    """
    js = _js()
    assert 'variant = (opts && opts.variant) || "danger"' in js
    mapping = re.search(r"els\.confirmOk\.className = ([^;]+);", js)
    assert mapping is not None, "confirmDialog must set the button's weight"
    expr = mapping.group(1)
    for token in ('"danger"', '"warn-btn"', '"primary"'):
        assert token in expr, f"confirm variant {token} is not mapped to a style"
    css = APP_CSS.read_text(encoding="utf-8")
    for cls in ("button.warn-btn", "button.primary", "button.danger"):
        assert cls in css, f"style {cls} is referenced but not defined"


def test_the_unicode_width_table_is_loaded_before_any_data() -> None:
    """CJK columns need Unicode 11 widths and a CJK monospace face.

    xterm's bundled table is Unicode v6: every CJK glyph then occupies the
    wrong number of cells and the line slides. Both halves are required --
    the width table *and* a font whose CJK advance is twice its Latin one.
    """
    js = _js()
    html = INDEX.read_text(encoding="utf-8")
    assert "addon-unicode11.js" in html, "the width-table addon must be shipped"
    assert "Unicode11Addon.Unicode11Addon()" in js
    assert 'term.unicode.activeVersion = "11"' in js
    # Loaded in createTab before ``term.open``/any write.
    load_at = js.index("Unicode11Addon.Unicode11Addon()")
    open_at = js.index("term.open(pane)")
    assert load_at < open_at, "the width table must be active before any data"
    # And the font stack has to name at least one CJK monospace.
    assert "Sarasa Mono SC" in js and "Noto Sans Mono CJK SC" in js


def test_accelerated_renderers_are_shipped_with_a_fallback() -> None:
    """WebGL first, Canvas second, DOM always available.

    WebGL will not initialise on every machine (remote desktop, software GL).
    A failure to accelerate must degrade, never leave the terminal blank.
    """
    js = _js()
    html = INDEX.read_text(encoding="utf-8")
    for asset in ("addon-webgl.js", "addon-canvas.js"):
        assert asset in html, f"{asset} must be shipped"
    assert "WebglAddon.WebglAddon()" in js
    assert "CanvasAddon.CanvasAddon()" in js
    assert 'renderer = "webgl"' in js and 'renderer = "canvas"' in js and 'renderer = "dom"' in js
    # Every catch degrades to the DOM renderer rather than rethrowing.
    assert js.count('catch { renderer = "dom"; }') >= 1


def test_a_transfer_cannot_unload_the_zmodem_sentry() -> None:
    """Unloading mid-protocol rendered the handshake as text.

    That is real 花屏, and it also released the input lock so keystrokes went
    into the protocol stream. The switch is now refused while a transfer runs.
    """
    js = _js()
    assert "function toggleZmodem(" in js
    assert "if (!s || s.zmodemActive) return;" in js
    assert "function syncZmodemButton(" in js
    assert "item.disabled = busy" in js


def test_a_resync_drops_live_frames_rather_than_duplicating_them() -> None:
    """The replay is a superset of whatever arrives while it is in flight.

    Queueing those frames would draw them twice on top of the replay -- and
    dropping *everything* until the replay ends would drop the replay itself,
    which is what the first version did: it asked for a resync and threw the
    answer away. ``resync-begin`` is what separates the two.
    """
    js = _js()
    assert "if (s.resyncing) return;" in js
    assert 'case "resync-begin":' in js
    assert 'case "resynced":' in js
    assert "RESYNC_TIMEOUT_MS" in js, "a resync must not write-lock the terminal forever"


def test_screen_text_is_readable_on_every_renderer() -> None:
    """"What does the terminal say" must not depend on how it was drawn.

    WebGL and Canvas paint to a canvas and leave no ``.xterm-rows`` DOM behind,
    so reading the page cannot answer that question -- and it is the first
    thing a support conversation asks. ``window.__wsctlScreen`` reads xterm's
    own buffer, which holds the same text whichever renderer drew it.
    """
    js = _js()
    assert "window.__wsctlScreen" in js
    assert "translateToString" in js


def test_a_hidden_tab_keeps_its_layout_box_until_it_is_really_done() -> None:
    """Unmount only tabs nobody is looking at, and re-measure only on change.

    Two guards, one feature. ``.term-pane.mounted`` is what stops a hidden pane
    from collapsing to 0x0 (and painting a blank frame on every switch), so
    unmounting the *active* tab would blank the screen the user is reading. And
    the point of keeping the box is to skip the re-measure -- which is only
    true if ``needsRefit`` is actually consulted rather than always fitting.
    """
    js = _js()
    css = (ROOT / "src" / "wsctl" / "static" / "app.css").read_text(encoding="utf-8")
    assert "if (s.id !== activeId) s.pane.classList.remove(\"mounted\");" in js, (
        "suspending the visible tab would blank it"
    )
    assert "if (s.needsRefit || !s.everShown)" in js, (
        "a tab switch must not re-measure when nothing changed"
    )
    # On the rule *body*, not the whole file: the comment above this rule says
    # "``visibility: hidden``, not ``display: none``" and would satisfy a naive
    # substring check even after the rule had been changed back to
    # ``display: none``. A guard that the code can pass while being wrong is
    # not a guard.
    body = re.search(r"\.term-pane\.mounted\s*\{[^}]*\}", css)
    assert body is not None, ".term-pane.mounted rule disappeared"
    assert "visibility: hidden" in body.group(0), (
        "a mounted pane must keep its layout box (visibility, not display: none)"
    )
    assert "display: none" not in body.group(0), (
        "display:none collapses the box and brings the blank frame back"
    )


def test_working_dialogs_are_wide_enough_for_their_own_content() -> None:
    """Four dialogs, sized for what is inside them rather than how many fields.

    A two-column SSH block, a session row of name + meta + two actions, a
    six-column audit table and a two-column key-binding grid all wrap badly in
    a 360px card. This pins both halves: each dialog asks for a size tier, and
    that tier is actually wide enough -- the tiers are the thing that silently
    shrinks when someone "tidies up" a number.
    """
    html = INDEX.read_text(encoding="utf-8")
    css = (ROOT / "src" / "wsctl" / "static" / "app.css").read_text(encoding="utf-8")

    def card_class(anchor: str) -> str:
        m = re.search(re.escape(anchor) + r'.*?class="([^"]*modal-card[^"]*)"', html, re.S)
        assert m is not None, f"no modal card near {anchor}"
        return m.group(1)

    def tier_width(name: str) -> float:
        m = re.search(rf"\.modal-card\.{name}\s*\{{[^}}]*width:\s*([0-9.]+)px", css)
        assert m is not None, f"tier .modal-card.{name} is not defined"
        return float(m.group(1))

    widths = {name: tier_width(name) for name in ("sm", "md", "lg", "xl")}
    # A monotone ladder: if two tiers ever collapse into one another, dialogs
    # silently stop being distinguishable in width.
    assert widths["sm"] < widths["md"] < widths["lg"] < widths["xl"], widths

    want = {
        "new-session-form": "lg",   # two-column SSH block
        "sessions-overlay": "lg",   # name + meta + two actions per row
        "admin-overlay": "xl",      # six-column audit table
        "settings-overlay": "lg",   # two-column key-binding grid
    }
    for anchor, tier in want.items():
        cls = card_class(anchor)
        assert tier in cls.split(), f"{anchor} should ask for {tier}, got {cls!r}"
        assert widths[tier] >= 700, (
            f"{anchor} uses {tier} at {widths[tier]:.0f}px, which is too narrow "
            "for the content it lays out"
        )


# -- 0.1.17 连接生命周期收口 ------------------------------------------------
#
# The 0.1.15/0.1.16 contracts guard rendering and dialogs. These guard the
# state machine: who is allowed to write the screen, where a replay starts, and
# when a status flag is allowed to contradict what the code does next. Each one
# is a defect that shipped because an *action* and a *declaration* drifted.


def test_a_new_socket_attempt_starts_from_a_clean_transient_state() -> None:
    """`connect` must not inherit the previous attempt's timers or queues.

    A backoff timer left pending alongside a manual connect opened **two**
    WebSockets for one tab (the second `s.ws = ws` orphaned the first, which
    stayed attached server-side). A write queue left over from the previous
    connection interleaveed with the new replay. An ``resyncing`` lock left
    over dropped the new attach replay on the floor -- a blank screen.
    """
    js = _js()
    assert "function resetTransients(" in js
    # Called at the top of connect, before a socket is created.
    connect_at = js.index("function connect(s)")
    reset_call = js.index("resetTransients(s)", connect_at)
    socket_at = js.index("new WebSocket(", connect_at)
    assert reset_call < socket_at, "resetTransients must run before the socket is created"
    body = js[js.index("function resetTransients(") : js.index("function ensureConnected(")]
    for token in ("s.reconnectTimer", "s.writeQueue", "s.resyncing", "s.resyncTimer"):
        assert token in body, f"resetTransients does not clear {token}"


def test_stale_socket_callbacks_are_inert() -> None:
    """A replaced socket's handlers must not touch the session.

    Without a generation check a stale `onclose` scheduled a second reconnect
    alongside the live one and a stale `onmessage` wrote the old connection's
    bytes into the new connection's screen.
    """
    js = _js()
    assert "const current = () => s.ws === ws;" in js
    # Every handler must consult it. onopen / onmessage / onclose / onerror.
    handlers = (
        "ws.onopen = () => {",
        "ws.onmessage = (event) => {",
        "ws.onclose = (event) => {",
    )
    for handler in handlers:
        at = js.index(handler)
        window = js[at : at + 400]
        assert "current()" in window, f"{handler} does not check the socket generation"
    # And a replaced socket is closed rather than leaked.
    assert "try { s.ws.close(); } catch { /* 忽略 */ }" in js


def test_a_new_attach_supersedes_an_inflight_resync() -> None:
    """`onopen` must lift the resync write-lock.

    ``enqueueWrite`` drops frames while ``resyncing`` is set -- which is right
    for live duplicates and catastrophic for the attach *replay* after a
    reconnect: the replay is a superset of the resync and would be dropped,
    leaving a blank screen until the resync timed out.
    """
    js = _js()
    onopen = js.index("ws.onopen = () => {")
    attached = js.index('case "attached":')
    window = js[onopen : onopen + 1400]
    assert "s.resyncing = false" in window, "onopen must lift the resync write-lock"
    assert "s.resyncTimer" in window and "clearTimeout" in window
    # `attached` (the end of the replay) does the same, belt and braces.
    attached_window = js[attached : js.index('case "exit":', attached)]
    assert "s.resyncing = false" in attached_window


def test_desync_is_dropped_by_a_clean_attach_replay_and_kept_by_a_shedding_one() -> None:
    """The bar must say true things.

    Client-side shedding only sheds the *delivery queue*; the server's
    scrollback is whole. A reconnect therefore repairs the screen, and a bar
    still claiming "内容已省略" is a lie. Unless the replay itself shed -- then
    the screen is still incomplete and the bar must stay.
    """
    js = _js()
    assert "s.shedSinceOpen" in js
    attached = js.index('case "attached":')
    window = js[attached : attached + 1600]
    # The clean case must clear; the incomplete case must keep (or raise) it.
    assert "clearDesync(s)" in window
    assert "msg.incomplete" in window and "s.shedSinceOpen" in window
    # A `desync` marks the flag; a fresh attempt resets it.
    desync = js.index('case "desync":')
    assert "s.shedSinceOpen = true" in js[desync : desync + 400]
    connect_at = js.index("function connect(s)")
    assert "s.shedSinceOpen = false" in js[connect_at : connect_at + 900]


def test_resync_must_not_announce_success_over_its_own_shed_frames() -> None:
    """`resynced` carries the server's verdict; the client believes it.

    A resync of a large buffer can shed too -- and what it sheds is the head of
    the screen being rebuilt. Clearing the bar then is the same lie the bar
    exists to prevent. The server compares ``dropped_events`` across the
    replay and sends ``incomplete``; the client keeps the bar when it is set.
    """
    js = _js()
    ws = (ROOT / "src" / "wsctl" / "server" / "ws.py").read_text(encoding="utf-8")
    assert "incomplete" in ws and "dropped_events" in ws
    resynced = js.index('case "resynced":')
    window = js[resynced : resynced + 800]
    assert "msg.incomplete" in window, "the client must honour the server's verdict"
    assert "clearDesync" in window


def test_attached_must_not_claim_a_whole_screen_over_its_own_shed_frames() -> None:
    """Same honesty rule on the attach path: `attached` carries a verdict too.

    The attach replay can shed (a small byte budget, or the frame cap when the
    stored chunk count is a TUI's PTY-read count). Saying "attached" as if the
    screen came back whole is what produced "lost sync" at an idle prompt with
    no explanation of what was actually missing.
    """
    js = _js()
    session = (ROOT / "src" / "wsctl" / "core" / "session.py").read_text(encoding="utf-8")
    assert '"incomplete": incomplete' in session, "attach must report its own sheds"
    assert "dropped_events" in session
    attached = js.index('case "attached":')
    window = js[attached : attached + 1600]
    assert "msg.incomplete" in window, "the client must honour the attach verdict"
    assert "markDesynced" in window


def test_an_eviction_notice_must_not_contradict_the_reconnect_that_follows() -> None:
    """`evicted` is recoverable: no standby claim, no manual-open nudge.

    4410 / memory eviction close the socket and `onclose` auto-reconnects.
    Marking the tab standby in between displayed "未连接" -- a state the very
    next line of code was about to contradict. The toast is the message.
    """
    js = _js()
    evicted = js.index('case "evicted":')
    window = js[evicted : js.index('case "error":', evicted)]
    # On the code, not the prose: the comment that explains the fix says the
    # word "standby", and a substring check on the block would fire on it --
    # exactly the "guard that the wrong code can pass" trap, inverted.
    assert "s.standby" not in window, "evicted must not mark the tab standby"
    assert "markTab(" not in window
    assert "setConnectionFor(" not in window
    assert "toast(" in window, "the user must still be told"


def test_a_paste_is_sent_in_chunks() -> None:
    """One huge frame fills the PTY write buffer in a single call.

    Its cap is checked only on entry, so a 500 KB paste makes every keystroke
    afterwards look like a dead keyboard. Chunking keeps the buffer draining.
    """
    js = _js()
    assert "function sendPaste(" in js
    assert "PASTE_CHUNK" in js
    assert "bytes.subarray(" in js
    # The paste path must use it (the keystroke path must not change).
    do_paste = js.index("function doPaste(")
    assert "sendPaste(" in js[do_paste : do_paste + 500]


def test_flushwrite_disarms_its_own_timer_on_every_entry() -> None:
    """`flushWrite` is called on the timer *and* directly.

    Nulling the id without `clearTimeout` left the old timer armed; the next
    `enqueueWrite` then saw a non-null id and refused to schedule, and the
    queued frames sat unflushed until the orphan fired. `requestResync` must
    also disarm after emptying the queue -- `flushWrite` may re-arm for the
    overflow it could not write this turn.
    """
    js = _js()
    flush = js.index("function flushWrite(")
    window = js[flush : flush + 400]
    assert "clearTimeout(s.writeTimer)" in window, (
        "flushWrite must disarm, not just null the id"
    )
    resync = js.index("function requestResync(")
    resync_window = js[resync : resync + 700]
    assert "s.writeQueue = []" in resync_window
    assert "clearTimeout(s.writeTimer)" in resync_window, (
        "requestResync drops the queue; any timer flushWrite re-armed must go too"
    )


def test_one_write_call_never_swallows_the_whole_replay() -> None:
    """`term.write` is capped so rebuilding the screen cannot eat a frame.

    A 4 MiB replay written whole is a multi-megabyte parse on the main thread
    exactly when the screen is being rebuilt. The cap is a changelog promise
    with no guard behind it until this test: "the terminal does not stutter"
    is not testable, "no single write exceeds 256 KiB" is.
    """
    js = _js()
    match = re.search(r"const WRITE_MAX_BYTES = (\d+) \* 1024;", js)
    assert match is not None, "WRITE_MAX_BYTES must exist and be spelled in KiB"
    assert int(match.group(1)) == 256, "the cap is a 256 KiB promise"
    flush = js.index("function flushWrite(")
    window = js[flush : js.index("function ", flush + 10)]
    assert "WRITE_MAX_BYTES" in window and "break" in window, (
        "flushWrite must stop merging at the cap"
    )
    # And the overflow is re-scheduled rather than stranded.
    assert "s.writeTimer = setTimeout" in window


def test_backoff_is_visible_and_can_be_skipped_by_clicking() -> None:
    """"重连中（Ns 后重试）" plus a click that retries *now*.

    A bare "已断开" could not be told apart from "gave up"; a remote operator
    has nothing to act on. The click is the "I fixed the network, stop
    waiting" path -- and it must go through the same single-shot `connect`.
    """
    js = _js()
    assert "重连中（" in js, "backoff must be named in the indicator"
    assert "点击立即重连" in js
    click_at = js.index('els.connection.addEventListener("click"')
    window = js[click_at : click_at + 500]
    assert "connect(s)" in window, "the click must retry immediately"
    assert "clearTimeout(s.reconnectTimer)" in window, (
        "the click must cancel the pending backoff, or two connects race"
    )


def test_search_input_is_debounced_like_every_other_filter() -> None:
    """"All matches" highlighting on every keystroke is a full buffer scan.

    The file and session filter boxes were debounced 250ms; the terminal search
    was not, and against a flood-filled session it stuttered per character.
    Enter still searches immediately.
    """
    js = _js()
    assert "searchDebounce" in js
    input_at = js.index('els.searchInput.addEventListener("input"')
    window = js[input_at : input_at + 700]
    assert "250" in window, "search input must be debounced"
    key_at = js.index('els.searchInput.addEventListener("keydown"')
    assert "clearTimeout" in js[key_at : key_at + 500], "Enter must flush the debounce"


def test_the_help_legend_and_the_reserved_set_cannot_drift() -> None:
    """One source of truth for "will a browser swallow this chord".

    ``BROWSER_RESERVED`` and the help sheet's grades were two lists. They
    disagreed about ``alt+ArrowLeft/Right`` -- which are *also* the default
    tab-switch chords -- and the set's mixed-case arrow entries never matched
    the lowercased lookup, so the bind-dialog warning never fired either.
    """
    js = _js()
    assert "function chordGrade(" in js
    assert "chordGrade(action, binding)" in js, "editable chords must be graded"
    assert "chordGrade(null, raw)" in js, "fixed chords must be graded the same way"
    # The set is lowercase-only and the arrows (a default binding) are not in it.
    reserved = re.search(r"const BROWSER_RESERVED = new Set\(\[([^\]]*)\]", js)
    assert reserved is not None
    entries = re.findall(r'"([^"]+)"', reserved.group(1))
    assert entries, "the reserved set is empty"
    assert all(e == e.lower() for e in entries), (
        f"reserved entries must be lowercase or the lookup misses them: {entries}"
    )
    assert "alt+arrowleft" not in entries and "alt+arrowright" not in entries
    # ...but they are still the defaults, so the contradiction is resolved in
    # the direction of "the page can cancel these" rather than by quietly
    # rebinding the user's tab switches.
    assert 'next_tab: "alt+ArrowRight"' in js
    assert 'prev_tab: "alt+ArrowLeft"' in js


def test_file_refresh_is_registered_once() -> None:
    """A duplicated listener is two requests and a flickering status line.

    Not a theoretical defect: the refresh button was bound twice at the bottom
    of the file-panel block.
    """
    js = _js()
    registrations = js.count('els.fileRefresh.addEventListener("click"')
    assert registrations == 1, f"file refresh bound {registrations} times"


def test_closing_the_preview_asks_about_unsaved_edits() -> None:
    """A stray click on the backdrop must not discard a page of typing."""
    js = _js()
    assert "previewDirtyBase" in js
    close_at = js.index("function closePreview(")
    window = js[close_at : close_at + 900]
    assert "previewDirtyBase" in window and "confirmDialog(" in window


def test_a_background_tab_may_never_rewrite_the_connection_indicator() -> None:
    """The indicator describes the *visible* terminal -- and its affordances.

    `setConnectionFor` guarded the text but not the title / cursor that say
    "click to retry". A background tab's `scheduleReconnect` therefore claimed
    the active tab's indicator and left it pointing at the wrong session.
    There is now exactly one painter, and it is guarded.
    """
    js = _js()
    assert "function paintConnectionFor(" in js
    # Two writers, both guarded: the painter (adds "click to retry") and
    # `setConnectionFor` (clears it). Anything else writing the affordances
    # is a background tab about to clobber the visible one.
    painters = ("function paintConnection(", "function setConnectionFor(")
    for name in painters:
        start = js.index(name)
        end = js.index("function ", start + len(name))
        block = js[start:end]
        assert "els.connection.style.cursor" in block, f"{name} must own the cursor state"
    stripped = js
    for name in painters:
        start = stripped.index(name)
        end = stripped.index("function ", start + len(name))
        stripped = stripped[:start] + stripped[end:]
    assert "els.connection.style.cursor" not in stripped, (
        "a third writer of the indicator cursor exists; background tabs will "
        "clobber the visible tab's state"
    )
    # Both backoff and connect paint through the guarded path.
    schedule = js.index("function scheduleReconnect(")
    assert "paintConnectionFor(s)" in js[schedule : schedule + 600]
    connect_at = js.index("function connect(s)")
    assert "paintConnectionFor(s)" in js[connect_at : connect_at + 900]
    assert "setConnectionFor(s," not in js[connect_at : connect_at + 900], (
        "connect must paint the whole indicator state, not just the text"
    )


def test_the_2fa_qr_matches_the_share_qr_scale() -> None:
    """Two codes, one visual scale -- 2FA allowed to be a shade larger.

    A desktop product: the phone comes to the monitor either way, so the codes
    should look related rather than one being a stamp and the other a poster.
    The contract is the *relationship* (never smaller than the share code),
    not a magic number: whatever the share code is sized to, 2FA follows.
    """
    css = APP_CSS.read_text(encoding="utf-8")

    def px(selector: str, prop: str) -> int:
        match = re.search(rf"\.{re.escape(selector)}\s*\{{[^}}]*?{prop}:\s*(\d+)px", css)
        assert match is not None, f".{selector} must declare {prop}"
        return int(match.group(1))

    share, _2fa = px("qr", "width"), px("qr-holder", "width")
    assert _2fa == px("qr-holder", "height"), "the QR holder must be square"
    assert share == px("qr", "height"), "the share code must be square"
    assert _2fa >= share, (
        f"the 2FA QR ({_2fa}px) is smaller than the share QR ({share}px); "
        "it is the one that must be scanned correctly on the first try"
    )
