"""wsctl terminal protocol v2.

The wire format is deliberately small and binary:

S→C data frame::

    +--------+------------------+--------+------------------+
    | kind=1 | seq (u64 big-end) | flags  | payload (bytes)  |
    +--------+------------------+--------+------------------+

C→S input frame::

    +--------+--------+------------------+
    | kind=2 | flags  | payload (bytes)  |
    +--------+--------+------------------+

``seq`` is the **global byte offset** of the first payload byte in the
session's output stream. It starts at zero, never resets while a session is
alive, and is the only number the client needs in order to resume exactly
where it left off.

Text frames are JSON control messages and must carry ``{"v": 2, "type": ...}``.
The decoder rejects unknown versions and unknown types rather than guessing:
a typo in a control frame must be a reported protocol error, not silently
ignored behaviour.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

try:  # orjson is a declared dependency; the fallback keeps stripped installs alive
    import orjson
except ImportError:  # pragma: no cover
    orjson = None  # type: ignore[assignment]


def _dumps(value: Any) -> str:
    if orjson is not None:
        return orjson.dumps(value).decode("utf-8")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(raw: str | bytes) -> Any:
    if orjson is not None:
        return orjson.loads(raw)
    return json.loads(raw)

PROTOCOL_VERSION = 2
SUBPROTOCOL = "wsctl.terminal.v2"

KIND_DATA = 1
KIND_INPUT = 2

FLAG_SNAPSHOT = 0b0000_0001
FLAG_REPLAY = 0b0000_0010
FLAG_LAST = 0b0000_0100

_HEADER = struct.Struct("!BQB")
_INPUT_HEADER = struct.Struct("!BB")

MAX_DATA_FRAME = 1024 * 1024
MAX_CONTROL_FRAME = 64 * 1024

CONTROL_TYPES = frozenset(
    {
        "attach",
        "attached",
        "ack",
        "resize",
        "ping",
        "pong",
        "gap",
        "snapshot",
        "snapshot-request",
        "resync",
        "resync-begin",
        "resynced",
        "resync-rejected",
        "exit",
        "notice",
        "error",
        "evicted",
        "command",
        "presence",
        "claim-control",
        "control",
        "renamed",
        "layout-changed",
    }
)


class ProtocolError(ValueError):
    """A frame or control message that does not satisfy protocol v2."""


@dataclass(frozen=True, slots=True)
class DataFrame:
    seq: int
    flags: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class InputFrame:
    flags: int
    payload: bytes


def encode_data(seq: int, payload: bytes, flags: int = 0) -> bytes:
    """Encode a server→client terminal data frame."""
    if seq < 0 or seq > 0xFFFFFFFFFFFFFFFF:
        raise ProtocolError("seq 超出 u64 范围")
    if len(payload) > MAX_DATA_FRAME:
        raise ProtocolError(f"数据帧超过上限（{len(payload)} > {MAX_DATA_FRAME}）")
    if flags & ~0x07:
        raise ProtocolError("未知的数据帧标志位")
    return _HEADER.pack(KIND_DATA, int(seq), int(flags)) + bytes(payload)


def decode_data(frame: bytes) -> DataFrame:
    """Decode a server→client terminal data frame."""
    if len(frame) < _HEADER.size:
        raise ProtocolError("数据帧头部不完整")
    kind, seq, flags = _HEADER.unpack_from(frame)
    if kind != KIND_DATA:
        raise ProtocolError(f"不是数据帧（kind={kind}）")
    if flags & ~0x07:
        raise ProtocolError("未知的数据帧标志位")
    payload = bytes(frame[_HEADER.size:])
    if len(payload) > MAX_DATA_FRAME:
        raise ProtocolError("数据帧超过上限")
    return DataFrame(seq=seq, flags=flags, payload=payload)


def encode_input(payload: bytes, flags: int = 0) -> bytes:
    """Encode a client→server terminal input frame."""
    if len(payload) > 256 * 1024:
        raise ProtocolError("输入帧超过上限")
    if flags & ~0x07:
        raise ProtocolError("未知的输入帧标志位")
    return _INPUT_HEADER.pack(KIND_INPUT, int(flags)) + bytes(payload)


def decode_input(frame: bytes) -> InputFrame:
    """Decode a client→server terminal input frame."""
    if len(frame) < _INPUT_HEADER.size:
        raise ProtocolError("输入帧头部不完整")
    kind, flags = _INPUT_HEADER.unpack_from(frame)
    if kind != KIND_INPUT:
        raise ProtocolError(f"不是输入帧（kind={kind}）")
    if flags & ~0x07:
        raise ProtocolError("未知的输入帧标志位")
    payload = bytes(frame[_INPUT_HEADER.size:])
    if len(payload) > 256 * 1024:
        raise ProtocolError("输入帧超过上限")
    return InputFrame(flags=flags, payload=payload)


def encode_control(message: dict[str, Any]) -> str:
    """Encode a control message, stamping the protocol version."""
    if not isinstance(message, dict):
        raise ProtocolError("控制消息必须是 JSON 对象")
    body = dict(message)
    body["v"] = PROTOCOL_VERSION
    kind = body.get("type")
    if not isinstance(kind, str) or kind not in CONTROL_TYPES:
        raise ProtocolError(f"未知的控制消息类型：{kind!r}")
    try:
        text = _dumps(body)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"控制消息不能序列化：{exc}") from exc
    if len(text.encode("utf-8")) > MAX_CONTROL_FRAME:
        raise ProtocolError("控制消息超过上限")
    return text


def decode_control(raw: str | bytes) -> dict[str, Any]:
    """Decode and validate a control message."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("控制消息不是 UTF-8") from exc
    if len(raw.encode("utf-8")) > MAX_CONTROL_FRAME:
        raise ProtocolError("控制消息超过上限")
    try:
        value = _loads(raw)
    except (ValueError, TypeError) as exc:
        raise ProtocolError(f"控制消息不是合法 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("控制消息必须是 JSON 对象")
    version = value.get("v", 1)
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"不支持的协议版本：{version!r}")
    kind = value.get("type")
    if not isinstance(kind, str) or kind not in CONTROL_TYPES:
        raise ProtocolError(f"未知的控制消息类型：{kind!r}")
    return value


__all__ = [
    "CONTROL_TYPES",
    "FLAG_LAST",
    "FLAG_REPLAY",
    "FLAG_SNAPSHOT",
    "KIND_DATA",
    "KIND_INPUT",
    "MAX_CONTROL_FRAME",
    "MAX_DATA_FRAME",
    "PROTOCOL_VERSION",
    "SUBPROTOCOL",
    "DataFrame",
    "InputFrame",
    "ProtocolError",
    "decode_control",
    "decode_data",
    "decode_input",
    "encode_control",
    "encode_data",
    "encode_input",
]
