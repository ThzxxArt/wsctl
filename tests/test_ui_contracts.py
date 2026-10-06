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


def _read(rel: str) -> str:
    """Read any source file in the repository, for cross-file contracts."""
    return (ROOT / rel).read_text(encoding="utf-8")


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
        "ws.onerror = () => {",
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
    # Not "the word appears somewhere in the file" -- a comment satisfies that.
    # The verdict must be *computed* from a before/after comparison, on both
    # the attach and the resync path.
    assert re.search(
        r"incomplete = int\(client\.dropped_events\) > before", ws
    ), "resynced must carry a real before/after verdict"
    session_src = (ROOT / "src" / "wsctl" / "core" / "session.py").read_text(encoding="utf-8")
    assert re.search(
        r"incomplete = int\(getattr\(client, \"dropped_events\", 0\)\) > before",
        session_src,
    ), "attached must carry a real before/after verdict"
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


def test_a_replay_is_followed_by_an_application_repaint() -> None:
    """Bytes cannot rebuild a TUI screen; the application itself must repaint.

    Replaying the byte stream is the wrong tool whenever the stream cannot be
    complete (shed holes, scrollback eviction): a full-screen app paints by
    cursor-addressed overwrites and lands them on a wrong base, and replaying
    the same bytes again reproduces the same garbage -- the "怎么弄都恢复不了"
    dead end. The app holds the only complete model and repaints on SIGWINCH;
    the server must issue that nudge wherever bytes were just replayed.
    """
    ws = (ROOT / "src" / "wsctl" / "server" / "ws.py").read_text(encoding="utf-8")
    session = (ROOT / "src" / "wsctl" / "core" / "session.py").read_text(encoding="utf-8")
    assert "async def nudge_repaint" in session
    assert "has_scrollback" in session
    assert ws.count("nudge_repaint()") >= 2, (
        "both replay paths (attach and resync) must end with an application repaint"
    )
    # Attach is gated on "was anything replayed"; resync is unconditional --
    # the user just asked for their screen back.
    assert "if session.has_scrollback:" in ws
    gate = ws.index("if session.has_scrollback:")
    call = ws.index("await session.nudge_repaint()", gate)
    assert "writable" not in ws[gate:call], (
        "a display refresh is not terminal input: read-only share viewers "
        "must get the repaint nudge too"
    )
    resync = ws.index('elif kind == "resync":')
    assert "nudge_repaint" in ws[resync : ws.index("except ClientGone", resync)]
    # The hint must not send the user to Ctrl+L (a TUI eats that key) or to a
    # manual window resize (the thing the product now does itself).
    html = INDEX.read_text(encoding="utf-8")
    hint = re.search(r'desync-hint">([^<]+)<', html)
    assert hint is not None, "the desync bar must explain what happens next"
    assert "Ctrl+L" not in hint.group(1), "Ctrl+L never reaches a TUI"
    assert "自动重绘" in hint.group(1), "the bar must say the app repaints for them"


def test_table_sort_direction_comes_from_the_table_state() -> None:
    """``sortRows`` must read the *table's* direction, not the column spec.

    ``spec`` is the column definition and never carried a direction; reading
    ``spec._dir`` (always ``undefined``) made every "descending" sort a lie --
    the header showed ↓ while the rows stayed ascending, and an operator
    looking for the newest record got the oldest.
    """
    js = _js()
    # Not a blanket "the substring is absent" -- the explanation comment names
    # the old bug. What must be absent is the *read*.
    assert "spec._dir &&" not in js and "spec._dir.key" not in js, (
        "the column spec is not a sort-state holder"
    )
    assert "sortRows(rows, state.sort, spec, state)" in js, (
        "sortRows must be handed the table state"
    )
    assert "state.sort === key && state.dir === -1" in js, (
        "the direction must come from state.dir"
    )


def test_a_totp_enrolment_is_not_active_before_it_is_confirmed() -> None:
    """Two-step enrolment: the secret is pending until a code verifies.

    The one-step version wrote the secret to the user row before the QR was
    even shown. A scan that never happened left the account demanding a code
    nobody had -- locked out of its own login.
    """
    py = _read("src/wsctl/server/routes/users.py")
    assert "_pending_totp" in py
    begin = py.index("async def enable_totp")
    confirm = py.index("async def confirm_totp")
    assert "user_set_totp" not in py[begin:confirm], (
        "the begin endpoint must not activate TOTP"
    )
    assert "user_set_totp" in py[confirm:], "confirm is what activates it"
    js = _js()
    assert "totp/confirm" in js, "the dialog must confirm before finishing"
    assert "qrConfirmed" in js, "closing without confirming must clean up"


def test_a_spoofed_xff_header_is_not_trusted() -> None:
    """``X-Forwarded-For`` is appended to: the LEFT end is client-controlled."""
    py = _read("src/wsctl/server/security.py")
    assert "reversed(hops)" in py, "the trusted end of the header is the right one"
    assert "split(\",\")[0]" not in py, "the leftmost hop is the attacker's"


def test_an_admin_password_never_reaches_the_process_table() -> None:
    """``--admin-password`` in argv is world-readable in ``/proc/*/cmdline``."""
    py = _read("src/wsctl/cli/main.py")
    assert '"admin_password": "--admin-password"' not in py, (
        "the bootstrap password must not be a child-process flag"
    )
    daemon = _read("src/wsctl/cli/daemon.py")
    assert "WSCTL_ADMIN_PASSWORD" in daemon, (
        "it travels in the child environment instead"
    )


def test_the_connect_client_reports_a_fatal_close_through_its_exit_code() -> None:
    """A revoked login is a failure. Returning 0 made ``&& echo ok`` lie."""
    py = _read("src/wsctl/cli/connect.py")
    assert "return 1" in py
    assert "if code in FATAL_CODES:" in py
    main = _read("src/wsctl/cli/main.py")
    assert "if code:" in main and "typer.Exit(code=code)" in main


def test_a_session_shell_cannot_claim_the_daemon_s_identity() -> None:
    """The lifecycle variables must not leak into a session's environment."""
    session = _read("src/wsctl/core/session.py")
    assert "WSCTL_ROLLING_FROM_PID" in session
    assert "session_env" in session
    daemon = _read("src/wsctl/cli/daemon.py")
    assert "LIFECYCLE_ENV_KEYS" in daemon


def test_ssh_options_that_execute_locally_are_not_forwarded() -> None:
    """``-o ProxyCommand=...`` is local command execution, not configuration."""
    py = _read("src/wsctl/core/ssh.py")
    assert "SAFE_OPTIONS" in py
    assert "ProxyCommand" in py, "the dangerous option must be named in the refusal"
    assert "_check_option" in py


def test_the_lifecycle_env_keys_have_one_definition() -> None:
    """The same three names in two modules is two chances to drift.

    ``core.closecodes`` exists because "three consumers, three lists" is how
    ``4401`` ended up fatal in one and not the others. This is the same shape:
    the keys the daemon sets for its child and the keys a *session* strips
    must be the same set, or a new lifecycle variable is added to one and
    leaks into every shell.
    """
    session = _read("src/wsctl/core/session.py")
    daemon = _read("src/wsctl/cli/daemon.py")
    core_def = re.search(r"^LIFECYCLE_ENV_KEYS = (\([^)]*\))", session, re.M)
    assert core_def, "core.session must own the list"
    assert "WSCTL_DAEMON" in core_def.group(1)
    assert "WSCTL_ROLLING_FROM_PID" in core_def.group(1)
    assert "WSCTL_LOG_FILE" in core_def.group(1)
    cli_def = re.search(r"^LIFECYCLE_ENV_KEYS = (\([^)]*\))", daemon, re.M)
    assert cli_def is None, (
        "cli.daemon keeps its own copy of the lifecycle env keys; it must "
        "import the single definition from core.session instead"
    )
    assert re.search(
        r"from wsctl\.core\.session import[^\n]*LIFECYCLE_ENV_KEYS", daemon
    ), "cli.daemon must take the keys from core.session"


# -- 0.1.24 M84: the declarations and the behaviour must agree ------------


def test_the_font_size_has_one_range_in_every_place_that_can_change_it() -> None:
    """Three sources, three answers -- until there is one source.

    The README promised 12-18, the Settings picker offered 12/13/14/16/18 and
    the Alt+E/D hotkey clamped at 9-24. A hotkey could therefore reach a size
    the picker could not show, and neither matched the docs. The picker is
    generated from the same two constants the hotkey clamps to.
    """
    js = _js()
    html = INDEX.read_text(encoding="utf-8")
    bounds = {}
    for name in ("FONT_MIN", "FONT_MAX"):
        match = re.search(rf"const {name} = (\d+);", js)
        assert match, f"{name} must be declared once"
        bounds[name] = int(match.group(1))
    assert bounds["FONT_MIN"] < bounds["FONT_MAX"]
    # The hotkey clamps to those constants, not to literals of its own.
    font_at = js.index("function changeFont(")
    block = js[font_at : font_at + 500]
    assert "FONT_MAX" in block and "FONT_MIN" in block, (
        "changeFont must clamp to the shared bounds rather than its own numbers"
    )
    assert re.search(r"Math\.min\(24|Math\.max\(9", block) is None, (
        "changeFont still carries its own 9-24 range"
    )
    # The picker is generated from them, so it cannot offer a size the hotkey
    # refuses -- and cannot offer fewer sizes than the range allows.
    assert "for (let size = FONT_MIN; size <= FONT_MAX; size += 1)" in js
    assert re.search(r"<select id=\"set-fontsize\"></select>", html), (
        "the picker must be filled from the constants, not hardcoded in HTML"
    )


def test_the_toast_dedupe_window_is_actually_used() -> None:
    """A constant that nothing reads is a promise the code does not keep.

    ``TOAST_DEDUPE_MS`` was declared and never referenced: the merge key was
    "is an identical card still alive", so a message repeating every four
    seconds for an hour was one card with a counter -- and a message repeating
    once a minute looked like a stream of new problems. The window now decides.
    """
    js = _js()
    match = re.search(r"const TOAST_DEDUPE_MS = (\d+);", js)
    assert match, "the dedupe window must be declared"
    assert int(match.group(1)) > 0
    # It must gate the lookup, and the entry must remember when it last fired
    # (otherwise a window cannot be measured at all).
    assert "TOAST_DEDUPE_MS" in js[js.index("function toast(") :], (
        "the dedupe window must be consulted by toast()"
    )
    assert js.count("TOAST_DEDUPE_MS") >= 2, (
        "TOAST_DEDUPE_MS is declared but never read"
    )
    assert "lastAt" in js, "a dedupe window needs a timestamp to measure from"


def test_the_renderer_can_be_switched_without_reloading_the_page() -> None:
    """A setting that only takes effect on F5 is a setting that never does.

    The renderer is baked into a Terminal at construction, so a switch is a
    rebuild -- which used to be documented as "reload the page". The rebuild
    now happens in place and asks for a replay, because a rebuilt terminal is
    blank and bytes cannot reconstruct a TUI screen on their own.
    """
    js = _js()
    assert "function buildTerminal(" in js, (
        "terminal construction must be reusable, not inline in createTab"
    )
    assert "function recreateTerminal(" in js
    # The rebuild must ask for the replay, or the user gets a blank terminal.
    rebuild_at = js.index("function recreateTerminal(")
    block = js[rebuild_at : rebuild_at + 1600]
    assert "requestResync(s)" in block, (
        "a rebuilt terminal is blank until a replay lands; the rebuild must ask"
    )
    assert "buildTerminal(s.pane)" in block
    # And the source may not tell the user to reload any more.
    assert "刷新页面后生效" not in js, (
        "the renderer switch is in place now; 'reload the page' is a lie"
    )
    # A transfer in flight must not have its protocol stream re-rendered as text.
    assert "if (s.zmodemActive)" in block


def test_the_hidden_tab_rule_is_still_the_one_that_keeps_the_box() -> None:
    """Pinned again here because the split layout rewrites the visibility model.

    ``.term-pane.mounted`` must still keep its layout box; only *which* panes
    get the ``shown`` class changed (a split shows several at once).
    """
    css = APP_CSS.read_text(encoding="utf-8")
    body = re.search(r"\.term-pane\.mounted\s*\{[^}]*\}", css)
    assert body is not None
    assert "visibility: hidden" in body.group(0)
    assert "display: none" not in body.group(0)
    assert re.search(r"\.term-pane\.mounted\.shown\s*\{[^}]*visibility: visible", css), (
        "a mounted pane becomes visible through `.shown`, not by dropping `mounted`"
    )


# -- 0.1.24 M79/M80: split panes ---------------------------------------


def test_a_split_is_a_layout_of_panes_not_a_second_terminal_model() -> None:
    """A tab owns a tree of panes; the session model is untouched.

    Every other part of this product still speaks "one tab per session" and
    the tests count tabs. Splitting must therefore be a *view*: the same
    ``sessions`` map, the same terminals, a tree that decides which panes are
    on screen together. A second model would be a second set of bugs.
    """
    js = _js()
    for name in ("leafIds(", "findLeaf(", "splitPane(", "closePane(", "renderLayout("):
        assert f"function {name}" in js, f"{name} is the layout's own vocabulary"
    # The tree has exactly two node shapes: a leaf names a session, a split
    # has a direction and a ratio. 2x2 is two splits, not a third type.
    assert '{ type: "leaf", sessionId' in js
    assert '{ type: "split", dir' in js
    # Closing a pane is not killing a session (0.1.1's promise).
    close_at = js.index("function detachTab(")
    assert "api(\"DELETE\"" not in js[close_at : close_at + 1400], (
        "detaching a pane must never terminate the session behind it"
    )


def test_a_tab_is_one_entry_however_many_panes_it_shows() -> None:
    """Two sessions side by side is still one thing in the tab bar.

    Two entries for one visual tab is the failure mode: the label names one
    session and the count names the other, and closing one leaves the other
    behind with no tab of its own.
    """
    js = _js()
    # The tab entry is created once and shared by every pane that joins it.
    create_at = js.index("function createTab(")
    block = js[create_at : create_at + 3200]
    assert "tabGroups.get(groupId)" in block, (
        "a second pane must join the existing tab entry, not add one"
    )
    assert block.index("els.tabs.appendChild(tabEl)") > block.index(
        "tabGroups.get(groupId)"
    ), "the tab entry is only created when the group has none"
    # The label names the focused session; the badge names how many there are.
    assert "function paintGroupTab(" in js
    assert "pane-count" in js


def test_a_split_never_loses_the_visible_pane_on_a_suspend() -> None:
    """The connection budget may suspend a pane; it may not blank one on screen.

    With several panes visible, "is this the focused one" is no longer the
    right question -- a split shows siblings that must not be unmounted
    either. The rule is "is it on screen", and the older form of it is still
    written out because it is still true.
    """
    js = _js()
    assert "function isPaneShown(" in js
    suspend_at = js.index("function suspendTab(")
    block = js[suspend_at : suspend_at + 1200]
    assert "isPaneShown(s)" in block, (
        "suspending must consult what is visible, not only what is focused"
    )
    assert 'if (s.id !== activeId) s.pane.classList.remove("mounted");' in block
    # And the resize path fits every visible pane, not just the focused one.
    fit_at = js.index("function fitActiveSoon(")
    assert "isPaneShown(" in js[fit_at : fit_at + 900]


def test_the_split_shortcuts_are_not_browser_reserved() -> None:
    """A chord that DevTools or the browser takes is a chord that does nothing.

    ``Alt+\\`` and ``Alt+Shift+\\`` are free; the tab switcher keeps the bare
    ``Alt+<arrow>`` pair and focus movement takes the ``Alt+Shift`` form.
    """
    js = _js()
    assert 'split_right: "alt+\\\\"' in js
    assert 'split_down: "alt+shift+\\\\"' in js
    assert "focus_left:" in js and "focus_right:" in js
    reserved = js[js.index("const BROWSER_RESERVED") : js.index("const BROWSER_RESERVED") + 900]
    for chord in ("alt+\\\\", "alt+shift+\\\\", "alt+shift+arrowleft"):
        assert chord not in reserved.lower(), f"{chord} must not be browser-reserved"


def test_the_layout_is_remembered_across_a_reload() -> None:
    """Sessions outlive connections, so the workbench can be put back exactly.

    The tree references session ids; a session that has ended is dropped from
    it rather than left as a hole. What is *not* restored is content -- the
    server replays that on attach, which is the mechanism that already exists.
    """
    js = _js()
    assert 'const LAYOUT_KEY = "wsctl-layout"' in js
    assert "function saveLayout(" in js and "function loadLayout(" in js
    assert "function restoreLayout(" in js
    assert "function deserializeNode(" in js
    restore_at = js.index("function restoreLayout(")
    block = js[restore_at : restore_at + 1800]
    assert "sessions.has(" in block or "sessions.has(id)" in block, (
        "a layout may only restore panes whose session still exists"
    )
    assert "activateTab(" in block


# -- 0.1.24 M81: a background session must be able to get your attention --


def test_a_background_session_reports_bells_output_and_exits() -> None:
    """Everything xterm already knew about a background session was thrown away.

    ``onBell``, ``onTitleChange`` and output all existed; nothing listened. A
    long build in one tab finished silently in another, and the only way to
    know was to click through every tab.
    """
    js = _js()
    assert "function noteActivity(" in js
    assert "function paintPresence(" in js
    wire_at = js.index("function wireTerminal(")
    block = js[wire_at : wire_at + 1200]
    assert "onBell(" in block, "BEL is the application asking for attention"
    assert "onTitleChange(" in block
    assert "noteActivity(" in block
    # An exit is the single most useful thing a background session can say.
    exit_at = js.index('case "exit":')
    assert "noteActivity(" in js[exit_at : exit_at + 700]
    # And looking at a pane is what "read" means.
    assert "clearActivity(" in js


def test_a_system_notification_is_opt_in_and_never_an_error() -> None:
    """A permission prompt is a cost the user chooses to pay.

    Denied or unsupported must be a silent no-op: a feature that throws when
    the browser says no is a feature that breaks the page.
    """
    js = _js()
    assert "function maybeNotify(" in js
    notify_at = js.index("function maybeNotify(")
    block = js[js.index("function maybeNotify(") : notify_at + 1400]
    assert "Notification.permission" in block
    assert "prefs.notifySystem" in block
    # Shared viewers are watching, not operating: no notifications for them.
    assert "sharedMode" in block
    assert "catch" in block, "a refused notification must not surface as an error"


def test_the_font_size_the_picker_offers_is_the_range_the_hotkey_allows() -> None:
    """Re-pinned with the split work: the picker is generated, never hardcoded.

    An HTML option list and a JS clamp are two places to get wrong; generating
    the list from the same constants removes the second one.
    """
    html = INDEX.read_text(encoding="utf-8")
    js = _js()
    assert re.search(r"<select id=\"set-fontsize\"></select>", html)
    assert "fillFontSizes(" in js
    assert "for (let size = FONT_MIN; size <= FONT_MAX; size += 1)" in js


def test_the_layout_walk_accumulates_instead_of_rebinding_per_call() -> None:
    """A tree walk that loses its accumulator collects "no panes at all".

    ``out = out || []`` inside a recursive branch creates a *private* array
    the caller never sees, so every split collected as empty: no pane-count
    badge, a tab that looked suspended for no reason, and a "which pane is on
    screen" answer of "none". The accumulator is created once at the top and
    threaded through.
    """
    js = _js()
    for name in ("leafIds", "rawLeafIds"):
        at = js.index(f"function {name}(")
        block = js[at : at + 420]
        assert "out = out || [];" in block, (
            f"{name} must create the accumulator once, before the recursion"
        )
        assert "(out = out || []).push" not in block, (
            f"{name} rebinds the accumulator inside a branch and drops the result"
        )
        assert "leafIds(node.a, out)" in block or "rawLeafIds(node.a, out)" in block, (
            f"{name} must thread the accumulator through both children"
        )


def test_a_presence_signal_is_an_overlay_never_terminal_output() -> None:
    """The one place an operational notice must never go is the terminal buffer.

    Writing it there is the mistake this product already fixed once for server
    notices: it is replayed to every reconnect as if the program had printed
    it, and it scrolls the prompt away mid-command. The presence signals (bell
    badge, activity glow, page title, favicon) are all DOM state, so the check
    is that none of the paths that *write to the terminal* grew a notice.
    """
    js = _js()
    # The presence machinery touches classes, the document title and the
    # favicon -- never ``term.write``.
    for name in ("function noteActivity(", "function paintPresence(",
                 "function paintFavicon(", "function maybeNotify("):
        at = js.index(name)
        block = js[at : at + 1400]
        assert "term.write" not in block, (
            f"{name} writes into the terminal buffer; a presence signal is an overlay"
        )
    # And the terminal-writers are unchanged in kind: output, the exit line,
    # and nothing else.
    assert js.count('s.term.write(`\\r\\n\\x1b[33m[wsctl]') == 1, (
        "the exit line is the only control message allowed on screen"
    )


def test_the_layout_is_readable_without_the_dom() -> None:
    """"Why is this pane here" cannot be answered by reading the page.

    The DOM says two boxes exist; it does not say which tree produced them,
    which one has the keyboard, or how many sockets are live. That is the
    first question a support conversation about a split asks, and the answer
    has to be the same shape a test can assert on.
    """
    js = _js()
    assert "window.__wsctlLayout" in js
    at = js.index("window.__wsctlLayout")
    block = js[at : at + 900]
    for field in ("activeTabId", "activeId", "liveSockets", "tabs"):
        assert field in block, f"__wsctlLayout must report {field}"
    assert "serializeNode(" in block, "the tree must be reported as data"
    assert "leafIds(" in block


def test_a_context_menu_is_placed_inside_the_window() -> None:
    """A menu whose later items fall outside the window has no later items.

    The term menu grew to fifteen entries; near the bottom edge the last ones
    were unreachable and could not be clicked at all. Placement measures the
    menu after showing it and clamps -- it does not trust the pointer.
    """
    js = _js()
    assert "function placeMenu(" in js
    at = js.index("function placeMenu(")
    block = js[at : js.index("function ", at + 20)]
    assert "getBoundingClientRect(" in block
    assert "innerWidth" in block and "innerHeight" in block
    # Every context menu goes through it; none sets left/top by hand.
    for name in ("openTermMenu", "openTabMenu", "openFileMenu"):
        start = js.index(f"function {name}(")
        end = js.index("\n  function ", start + 10)
        body = js[start:end]
        assert "placeMenu(" in body, f"{name} must place itself through placeMenu"
    assert "els.termMenu.style.left" not in js
    assert "els.tabMenu.style.left" not in js
    assert "els.fileMenu.style.left" not in js


def test_no_block_comment_is_left_open_in_the_frontend() -> None:
    """``node --check`` does not catch a comment that swallows the code.

    A ``/**`` written where ``*/` was meant (it happens when a docstring from
    another language gets pasted in) leaves the block comment open, and the
    functions after it become comment text. The file is still *syntactically*
    valid -- the comment closes at the next JSDoc block -- so the parser is
    happy and the feature is simply missing. This is the check that catches it.
    """
    js = _js()
    depth = 0
    index = 0
    while True:
        open_at = js.find("/*", index)
        close_at = js.find("*/", index)
        if open_at == -1 and close_at == -1:
            break
        if open_at != -1 and (close_at == -1 or open_at < close_at):
            depth += 1
            index = open_at + 2
        else:
            depth -= 1
            assert depth >= 0, f"stray */ at offset {close_at}"
            index = close_at + 2
    assert depth == 0, (
        f"{depth} unterminated block comment(s) in app.js; whatever follows is dead code"
    )
    assert '"""' not in js, "a Python docstring terminator has no business in JS"


# -- 0.1.24 回归 review：热路径与「回放不是新闻」 -----------------------


def test_presence_is_a_derived_view_painted_once_per_frame() -> None:
    """Per-frame DOM work in the output path is the defect this repo keeps
    fixing. Frame coalescing, write caps and resize merging (0.1.15–0.1.21)
    all exist to get it out -- so the presence signals must not put it back.

    ``noteActivity`` records state and schedules **one** paint; the favicon is
    redrawn only when the number it draws actually changes.
    """
    js = _js()
    at = js.index("function noteActivity(")
    block = js[at : js.index("\n  function ", at + 10)]
    assert "schedulePresence()" in block, "noteActivity must schedule, not paint"
    for forbidden in ("paintPresence()", "paintFavicon(", "document.title"):
        assert forbidden not in block, (
            f"noteActivity does {forbidden} inline; that is per-frame work"
        )
    # One paint per animation frame, and never two in flight.
    sched = js[js.index("function schedulePresence(") :]
    sched = sched[: sched.index("\n  function ")]
    assert "requestAnimationFrame(" in sched
    assert "if (presenceFrame) return;" in sched, (
        "a second call while one is in flight must not queue another paint"
    )
    # The favicon is the expensive one: an Image, a canvas pass and a
    # toDataURL. Only on a change of the number.
    fav = js[js.index("function paintFavicon(") :]
    fav = fav[: fav.index("\n  function ")]
    assert "if (count === lastFaviconCount) return;" in fav, (
        "the favicon must not be redrawn for the same count"
    )


def test_output_before_attached_is_a_replay_not_news() -> None:
    """A reconnect re-delivers the screen the user already had.

    The server enqueues the scrollback replay and then ``attached`` under the
    same lock (``core/session.py``), so ``attached`` is the fact that says the
    replay is over. Counting replayed bytes as activity made every network
    blip ring every background tab with "有新输出" -- a false alarm that
    teaches people to ignore the real ones.
    """
    js = _js()
    at = js.index("function noteActivity(")
    block = js[at : js.index("\n  function ", at + 10)]
    assert "if (!s.attached) return;" in block, (
        "presence must not fire for the attach replay"
    )
    # ...nor for the repaint the server triggers right after a replay. That
    # one is not marked on the wire at all, so it is suppressed by *when* it
    # arrives -- and the window must be short enough not to swallow an event.
    assert "ACTIVITY_GRACE_MS" in block, (
        "the post-attach repaint must not be reported as news"
    )
    grace = re.search(r"const ACTIVITY_GRACE_MS = (\d+);", js)
    assert grace and 0 < int(grace.group(1)) <= 2000, (
        "the grace window must be long enough for a repaint and short enough "
        "not to swallow a real one"
    )
    # And ``attached`` is the only thing that opens the gate -- reset on every
    # fresh socket attempt, so each reconnect re-closes it.
    connect_at = js.index("function connect(s)")
    assert "s.attached = false;" in js[connect_at : connect_at + 1200]
    handle_at = js.index('case "attached":')
    assert "s.attached = true;" in js[handle_at : handle_at + 400]


def test_presence_still_paints_when_the_page_is_not_visible() -> None:
    """``requestAnimationFrame`` is paused for a hidden page.

    That is precisely when the page title and the favicon are the only signals
    anyone can see -- an operator working in another browser tab is the whole
    audience for them. Coalescing must therefore survive backgrounding: a
    timer rides along with the frame callback and whichever fires first paints.
    """
    js = _js()
    sched = js[js.index("function schedulePresence(") :]
    sched = sched[: sched.index("\n  function ")]
    assert "requestAnimationFrame(" in sched, "frame-aligned while visible"
    assert "setTimeout(" in sched, (
        "a hidden page never fires requestAnimationFrame; the title and "
        "favicon would freeze at whatever they were when the user left"
    )
    # One shared tick, armed once and disarmed by whichever trigger gets
    # there first -- so an event is one paint however both fire.
    assert sched.count("const run =") == 1, "the two triggers must share one tick"
    assert sched.count("presenceFrame = 0;") == 1, (
        "the tick must disarm itself exactly once, so the second trigger is a no-op"
    )
    assert "run();" not in sched, "the tick must only be scheduled, never run inline"


def test_presence_marks_are_aggregated_per_tab_not_per_session() -> None:
    """A split puts several sessions on one tab entry.

    Writing the shared ``tabEl`` from a per-session walk let whichever session
    the Map reached last win: a live pane's glow was switched off by a quiet
    sibling, and a sibling with no bells deleted the badge another one had
    just created. Both end at "the tab says nothing" -- the one outcome this
    whole feature exists to prevent. The marks are computed per tab entry.
    """
    js = _js()
    paint = js[js.index("function paintPresence(") :]
    paint = paint[: paint.index("\n  // The favicon carries")]
    assert "const perTab = new Map();" in paint, (
        "presence marks must be aggregated per tab entry before being painted"
    )
    assert "entry.glow = entry.glow || active;" in paint, (
        "one live pane must light the tab even if a sibling is quiet"
    )
    assert "entry.bells += s.bellCount || 0;" in paint, (
        "bells must be summed across a split, not overwritten by the last pane"
    )
    # The badge is driven from the aggregate, so a pane with no bells can no
    # longer delete a sibling's.
    badge_at = paint.index("let badge = tabEl.querySelector")
    assert "entry.bells > 0" in paint[badge_at : badge_at + 500]


def test_opening_a_tab_marks_every_pane_in_it_read() -> None:
    """Everything on screen has been seen.

    A split shows several panes at once. Clearing only the focused one left
    its visible siblings reporting unread for output that is right in front
    of the user.
    """
    js = _js()
    at = js.index("function activateTab(")
    block = js[at : at + 3200]
    assert "for (const paneId of leafIds(group.root)) clearActivity(" in block, (
        "activating a tab must clear the unread state of every pane it shows"
    )
    assert block.count("clearActivity(") == 1, (
        "there must be exactly one call, over the whole group"
    )


def test_the_focus_fallback_never_picks_a_placeholder_pane() -> None:
    """A leaf whose session is gone cannot take the keyboard.

    ``activateTab`` needs a live session. Handing it a placeholder silently
    did nothing and left ``activeId`` pointing at the pane that had just been
    removed, after which every toolbar action quietly no-opped.
    """
    js = _js()
    at = js.index("function detachTab(")
    block = js[at : js.index("\n  function ", at + 10)]
    assert "sessions.has(paneId)" in block, (
        "the focus fallback must skip panes that have no session"
    )
    assert "const fallback = nextLive" in block
    assert block.count("activateTab(") == 1, "exactly one focus handover"
