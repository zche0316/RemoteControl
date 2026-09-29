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
    clicks: int = 1


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


@dataclass(frozen=True)
class SurfaceMatch:
    type: str = "surface.match"
    surface: str = "window"
    width: float = 0.0
    height: float = 0.0
    id: int | None = None


@dataclass(frozen=True)
class KeyDown:
    type: str = "key.down"
    code: str = ""
    key: str = ""
    modifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class KeyUp:
    type: str = "key.up"
    code: str = ""
    key: str = ""
    modifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class KeyReleaseAll:
    type: str = "key.releaseAll"


Command = (
    MouseMove
    | MouseClick
    | MouseDown
    | MouseUp
    | MouseScroll
    | KeyDown
    | KeyUp
    | KeyReleaseAll
    | Ping
    | ScreenInfo
    | SurfaceMatch
)

_ALLOWED_MODIFIERS = frozenset({"shift", "ctrl", "alt", "meta"})
_MAX_CODE_LEN = 32
_MAX_KEY_LEN = 16

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


def _require_code(data: dict[str, Any]) -> str:
    code = data.get("code")
    if not isinstance(code, str) or not code or len(code) > _MAX_CODE_LEN:
        raise ProtocolError("code must be a non-empty string")
    if not all(ch.isalnum() or ch in "._-" for ch in code):
        raise ProtocolError("code contains invalid characters")
    return code


def _optional_key(data: dict[str, Any]) -> str:
    if "key" not in data:
        return ""
    value = data["key"]
    if not isinstance(value, str) or len(value) > _MAX_KEY_LEN:
        raise ProtocolError("key must be a short string")
    return value


def _optional_modifiers(data: dict[str, Any]) -> tuple[str, ...]:
    if "modifiers" not in data:
        return ()
    value = data["modifiers"]
    if not isinstance(value, list):
        raise ProtocolError("modifiers must be an array")
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or item not in _ALLOWED_MODIFIERS:
            raise ProtocolError("modifiers must be shift, ctrl, alt, and/or meta")
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def _require_button(data: dict[str, Any]) -> str:
    button = data.get("button", "left")
    if not isinstance(button, str) or button not in _ALLOWED_BUTTONS:
        raise ProtocolError("button must be one of: left, right, middle")
    return button


def _optional_clicks(data: dict[str, Any]) -> int:
    value = data.get("clicks", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 3:
        raise ProtocolError("clicks must be an integer from 1 to 3")
    return value


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

    if msg_type == "surface.match":
        surface = data.get("surface") or "window"
        if not isinstance(surface, str) or surface not in {"window", "monitor", "browser", ""}:
            raise ProtocolError("surface must be window, monitor, or browser")
        return SurfaceMatch(
            surface=surface or "window",
            width=_require_number(data, "width"),
            height=_require_number(data, "height"),
            id=_optional_int(data, "id") if "id" in data else None,
        )

    if msg_type == "mouse.move":
        return MouseMove(x=_require_number(data, "x"), y=_require_number(data, "y"))

    if msg_type == "mouse.click":
        return MouseClick(
            x=_require_number(data, "x"),
            y=_require_number(data, "y"),
            button=_require_button(data),
            clicks=_optional_clicks(data),
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

    if msg_type == "key.down":
        return KeyDown(
            code=_require_code(data),
            key=_optional_key(data),
            modifiers=_optional_modifiers(data),
        )

    if msg_type == "key.up":
        return KeyUp(
            code=_require_code(data),
            key=_optional_key(data),
            modifiers=_optional_modifiers(data),
        )

    if msg_type == "key.releaseAll":
        return KeyReleaseAll()

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
