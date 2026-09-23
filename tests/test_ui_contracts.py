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
