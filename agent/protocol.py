"""JSON message parse/validate for the WebSocket control protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ProtocolError(ValueError):
    """Invalid or unsupported client message."""


@dataclass(frozen=True)
class MouseMove:
    type: str = "mouse.move"
    x: float = 0.0
    y: float = 0.0


@dataclass(frozen=True)
class MouseClick:
    type: str = "mouse.click"
    x: float = 0.0
    y: float = 0.0
    button: str = "left"


@dataclass(frozen=True)
class MouseDown:
    type: str = "mouse.down"
    x: float = 0.0
    y: float = 0.0
    button: str = "left"


@dataclass(frozen=True)
class MouseUp:
    type: str = "mouse.up"
    x: float = 0.0
    y: float = 0.0
    button: str = "left"


@dataclass(frozen=True)
class MouseScroll:
    type: str = "mouse.scroll"
    x: float = 0.0
    y: float = 0.0
    dx: int = 0
    dy: int = 0


@dataclass(frozen=True)
class Ping:
    type: str = "ping"


@dataclass(frozen=True)
class ScreenInfo:
    type: str = "screen.info"


Command = MouseMove | MouseClick | MouseDown | MouseUp | MouseScroll | Ping | ScreenInfo

_ALLOWED_BUTTONS = frozenset({"left", "right", "middle"})


def _require_number(data: dict[str, Any], key: str) -> float:
    if key not in data:
        raise ProtocolError(f"missing field: {key}")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{key} must be a number")
    return float(value)


def _optional_int(data: dict[str, Any], key: str, default: int = 0) -> int:
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{key} must be an integer")
    return int(value)


def _require_button(data: dict[str, Any]) -> str:
    button = data.get("button", "left")
    if not isinstance(button, str) or button not in _ALLOWED_BUTTONS:
        raise ProtocolError("button must be one of: left, right, middle")
    return button


def parse_message(data: Any) -> Command:
    if not isinstance(data, dict):
        raise ProtocolError("message must be a JSON object")

    msg_type = data.get("type")
    if not isinstance(msg_type, str) or not msg_type:
        raise ProtocolError("missing or invalid field: type")

    if msg_type == "ping":
        return Ping()

    if msg_type == "screen.info":
        return ScreenInfo()

    if msg_type == "mouse.move":
        return MouseMove(x=_require_number(data, "x"), y=_require_number(data, "y"))

    if msg_type == "mouse.click":
        return MouseClick(
            x=_require_number(data, "x"),
            y=_require_number(data, "y"),
            button=_require_button(data),
        )

    if msg_type == "mouse.down":
        return MouseDown(
            x=_require_number(data, "x"),
            y=_require_number(data, "y"),
            button=_require_button(data),
        )

    if msg_type == "mouse.up":
        return MouseUp(
            x=_require_number(data, "x"),
            y=_require_number(data, "y"),
            button=_require_button(data),
        )

    if msg_type == "mouse.scroll":
        return MouseScroll(
            x=_require_number(data, "x"),
            y=_require_number(data, "y"),
            dx=_optional_int(data, "dx", 0),
            dy=_optional_int(data, "dy", 0),
        )

    raise ProtocolError(f"unsupported type: {msg_type}")


def ok_response(msg_type: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": True, "type": msg_type}
    body.update(extra)
    return body


def error_response(error: str, msg_type: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": False, "error": error}
    if msg_type is not None:
        body["type"] = msg_type
    return body
