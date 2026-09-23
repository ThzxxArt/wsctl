"""WCAG 2.1 non-text contrast for the design tokens in ``static/app.css``.

The 0.1.8 plan promised "对比度 AA" and the first audit found one real
failure: the form-control border sat at 1.5:1 on both themes. Greys that only
separate regions are exempt as decoration (WCAG 2.1 1.4.11), but a control's
boundary is what *identifies* it, so ``--border-input`` must clear 3:1 against
every background a control can sit on. Body text must clear 4.5:1.

This test is what keeps the promise from decaying the next time someone
"just tweaks a colour".
"""

from __future__ import annotations

import re
from pathlib import Path

APP_CSS = Path(__file__).resolve().parent.parent / "src" / "wsctl" / "static" / "app.css"


def _block(selector: str, css: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    assert match is not None, f"selector not found: {selector}"
    return match.group(1)


def _tokens(selector: str, css: str) -> dict[str, str]:
    found = re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", _block(selector, css))
    return dict(found)


def _luminance(value: str) -> float:
    text = value.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    channels = [int(text[i : i + 2], 16) / 255 for i in (0, 2, 4)]

    def linearise(u: float) -> float:
        return u / 12.92 if u <= 0.03928 else ((u + 0.055) / 1.055) ** 2.4

    r, g, b = (linearise(c) for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# (foreground, background, minimum) -- 4.5 for body text, 3.0 for UI boundaries.
BODY_TEXT = [
    ("fg", "bg", 4.5),
    ("fg", "bg-2", 4.5),
    ("fg", "bg-3", 4.5),
    ("fg", "bg-4", 4.5),
    ("muted", "bg-2", 4.5),
    ("muted", "bg-3", 4.5),
]
# A control's boundary and the focus ring identify the control.
UI_BOUNDARY = [
    ("border-input", "bg", 3.0),
    ("border-input", "bg-2", 3.0),
    ("border-input", "bg-3", 3.0),
    ("accent", "bg", 3.0),
    ("accent", "bg-2", 3.0),
]
SEMANTIC = [
    ("ok", "bg-2", 3.0),
    ("warn", "bg-2", 3.0),
    ("danger", "bg-2", 3.0),
]


def test_dark_theme_meets_wcag_aa() -> None:
    tokens = _tokens(":root", APP_CSS.read_text(encoding="utf-8"))
    failures = []
    for fg, bg, need in BODY_TEXT + UI_BOUNDARY + SEMANTIC:
        actual = _ratio(tokens[fg], tokens[bg])
        if actual < need:
            failures.append(f"{fg} on {bg}: {actual:.2f}:1 < {need}")
    assert not failures, "dark theme below WCAG AA:\n  " + "\n  ".join(failures)


def test_light_theme_meets_wcag_aa() -> None:
    tokens = _tokens('html[data-theme="light"]', APP_CSS.read_text(encoding="utf-8"))
    failures = []
    for fg, bg, need in BODY_TEXT + UI_BOUNDARY + SEMANTIC:
        actual = _ratio(tokens[fg], tokens[bg])
        if actual < need:
            failures.append(f"{fg} on {bg}: {actual:.2f}:1 < {need}")
    assert not failures, "light theme below WCAG AA:\n  " + "\n  ".join(failures)


def test_interactive_controls_use_the_accessible_border() -> None:
    """Form controls must not fall back to the decorative border grey.

    ``--border`` / ``--border-strong`` are exempt as decoration; a control
    drawn with them is not.
    """
    css = APP_CSS.read_text(encoding="utf-8")
    # Every rule that styles an input/select/textarea/button border must use
    # the token this test proves is legible. The list grows whenever a new kind
    # of control appears -- 0.1.15 added ghost/warn actions, the settings
    # dialog's key-binding inputs and the search toggles -- and a control that
    # slips past this list is exactly how the 0.1.8 failure happened.
    for pattern in (
        r"\.modal-card input, \.modal-card select, \.modal-card textarea "
        r"\{[^}]*border: 1px solid var\(--(?P<tok>[a-z-]+)\)",
        r"\.modal-actions button \{[^}]*border: 1px solid var\(--(?P<tok>[a-z-]+)\)",
        r"\.modal-card button\.ghost,\s*\.modal-card button\.warn-btn "
        r"\{[^}]*border: 1px solid var\(--(?P<tok>[a-z-]+)\)",
        r"\.admin-toolbar input \{[^}]*border: 1px solid var\(--(?P<tok>[a-z-]+)\)",
        r"\.hotkey-row input \{[^}]*border: 1px solid var\(--(?P<tok>[a-z-]+)\)",
        r"\.custom-theme input, \.custom-theme textarea "
        r"\{[^}]*border: 1px solid var\(--(?P<tok>[a-z-]+)\)",
        r"#file-filter \{[^}]*border: 1px solid var\(--(?P<tok>[a-z-]+)\)",
        r"\.search-bar \.opt \{[^}]*border: 1px solid var\(--(?P<tok>[a-z-]+)\)",
    ):
        match = re.search(pattern, css, re.S)
        assert match is not None, f"rule not found: {pattern[:44]}"
        assert match.group("tok") == "border-input", (
            f"interactive control uses --{match.group('tok')}, which is decorative"
        )


def test_focus_ring_is_visible() -> None:
    """:focus-visible is the only keyboard affordance; it must clear 3:1."""
    css = APP_CSS.read_text(encoding="utf-8")
    block = _block(":focus-visible", css)
    assert "var(--accent)" in block, "focus ring must use the accent token"
    for name, selector in (("dark", ":root"), ("light", 'html[data-theme="light"]')):
        tokens = _tokens(selector, css)
        assert _ratio(tokens["accent"], tokens["bg"]) >= 3.0, name
        assert _ratio(tokens["accent"], tokens["bg-2"]) >= 3.0, name
