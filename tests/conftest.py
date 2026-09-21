from __future__ import annotations

from typing import Any


class FakeClient:
    """In-memory client sink used by the tests."""

    def __init__(self) -> None:
        self.items: list[bytes | dict[str, Any]] = []

    def put(self, item: bytes | dict[str, Any]) -> None:
        self.items.append(item)

    def output(self) -> bytes:
        return b"".join(i for i in self.items if isinstance(i, bytes))

    def controls(self, kind: str) -> list[dict[str, Any]]:
        return [i for i in self.items if isinstance(i, dict) and i.get("type") == kind]
