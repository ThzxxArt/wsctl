"""Release-facing documentation invariants.

The PyPI badge must stay dynamic; the SVG banner is a static asset and cannot
query PyPI, so it must name the release family instead of a patch version that
goes stale the next time a patch is cut.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_pypi_badge_is_dynamic() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "img.shields.io/pypi/v/wsctl" in readme


def test_banner_names_the_release_family_not_a_patch() -> None:
    banner = (ROOT / "docs" / "banner.svg").read_text(encoding="utf-8")
    assert "0.2.x" in banner
    assert re.search(r"\b0\.2\.\d+\b", banner) is None, (
        "docs/banner.svg pins a patch version; use the 0.2.x family so a "
        "README-only patch release does not require editing the SVG"
    )
