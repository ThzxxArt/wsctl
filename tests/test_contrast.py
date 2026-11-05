"""WCAG 2.1 contrast for the 0.2.0 design tokens.

The old suite asserted against ``static/app.css``, the hand-written 0.1.x
stylesheet. 0.2.0 is a Vite build: the source of truth is ``web/src/styles.css``
and the released file is content-hashed. Checking the source keeps the promise
from decaying when someone "just tweaks a colour", while the separate asset
test proves the built CSS is actually the one shipped.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_CSS = Path(__file__).resolve().parent.parent / "web" / "src" / "styles.css"


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


BODY_TEXT = [
    ("text", "bg", 4.5),
    ("text", "bg-elevated", 4.5),
    ("text", "bg-panel", 4.5),
    ("muted", "bg", 4.5),
    ("muted", "bg-elevated", 4.5),
]
UI_BOUNDARY = [
    ("border-input", "bg", 3.0),
    ("border-input", "bg-elevated", 3.0),
    ("accent", "bg", 3.0),
    ("accent", "bg-elevated", 3.0),
]
SEMANTIC = [
    ("success", "bg-elevated", 3.0),
    ("warning", "bg-elevated", 3.0),
    ("danger", "bg-elevated", 3.0),
]


def _check(selector: str, label: str) -> None:
    tokens = _tokens(selector, APP_CSS.read_text(encoding="utf-8"))
    failures = []
    for fg, bg, need in BODY_TEXT + UI_BOUNDARY + SEMANTIC:
        actual = _ratio(tokens[fg], tokens[bg])
        if actual < need:
            failures.append(f"{fg} on {bg}: {actual:.2f}:1 < {need}")
    assert not failures, f"{label} theme below WCAG AA:\n  " + "\n  ".join(failures)


def test_dark_theme_meets_wcag_aa() -> None:
    _check(":root", "dark")


def test_light_theme_meets_wcag_aa() -> None:
    _check(':root[data-theme="light"]', "light")


def test_focus_ring_is_visible() -> None:
    """:focus-visible is the only keyboard affordance; it must clear 3:1."""
    css = APP_CSS.read_text(encoding="utf-8")
    assert "outline: 2px solid var(--accent)" in css
    for selector, label in ((":root", "dark"), (':root[data-theme="light"]', "light")):
        tokens = _tokens(selector, css)
        assert _ratio(tokens["accent"], tokens["bg"]) >= 3.0, label
        assert _ratio(tokens["accent"], tokens["bg-elevated"]) >= 3.0, label


def test_reduced_motion_is_respected() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in css
