"""Synthetic mouse events via CoreGraphics CGEventPost."""

from __future__ import annotations

import math
import time

from Quartz import (
    CGDisplayBounds,
    CGEventCreate,
    CGEventCreateMouseEvent,
    CGEventCreateScrollWheelEvent,
    CGEventGetLocation,
    CGEventPost,
    CGEventSetIntegerValueField,
    CGMainDisplayID,
    CGPointMake,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseUp,
    kCGEventOtherMouseDown,
    kCGEventOtherMouseUp,
    kCGEventRightMouseDown,
    kCGEventRightMouseUp,
    kCGEventMouseMoved,
    kCGHIDEventTap,
    kCGMouseButtonCenter,
    kCGMouseButtonLeft,
    kCGMouseButtonRight,
    kCGMouseEventDeltaX,
    kCGMouseEventDeltaY,
    kCGScrollEventUnitLine,
)

# (down_type, up_type, button_constant)
_BUTTON_MAP = {
    "left": (kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGMouseButtonLeft),
    "right": (kCGEventRightMouseDown, kCGEventRightMouseUp, kCGMouseButtonRight),
    "middle": (kCGEventOtherMouseDown, kCGEventOtherMouseUp, kCGMouseButtonCenter),
}


def _point(x: float, y: float):
    return CGPointMake(float(x), float(y))


def _post(event) -> None:
    if event is None:
        raise RuntimeError("failed to create CGEvent")
    CGEventPost(kCGHIDEventTap, event)


def main_display_bounds() -> dict[str, float]:
    """Primary display bounds in CoreGraphics global coordinates (top-left origin)."""
    bounds = CGDisplayBounds(CGMainDisplayID())
    return {
        "x": float(bounds.origin.x),
        "y": float(bounds.origin.y),
        "width": float(bounds.size.width),
        "height": float(bounds.size.height),
    }


def current_position() -> tuple[float, float]:
    event = CGEventCreate(None)
    if event is None:
        return (0.0, 0.0)
    loc = CGEventGetLocation(event)
    return (float(loc.x), float(loc.y))


def move(x: float, y: float, dx: float | None = None, dy: float | None = None) -> None:
    """Move the cursor to absolute screen coordinates (top-left origin)."""
    point = _point(x, y)
    event = CGEventCreateMouseEvent(None, kCGEventMouseMoved, point, kCGMouseButtonLeft)
    if dx is not None:
        CGEventSetIntegerValueField(event, kCGMouseEventDeltaX, int(round(dx)))
    if dy is not None:
        CGEventSetIntegerValueField(event, kCGMouseEventDeltaY, int(round(dy)))
    _post(event)


def move_interpolated(
    x: float,
    y: float,
    origin: tuple[float, float] | None = None,
    max_step: float = 8.0,
    max_steps: int = 48,
) -> tuple[float, float]:
    """Move to (x, y), inserting short steps when the jump is large. Returns the final position."""
    start = origin if origin is not None else current_position()
    dx = float(x) - start[0]
    dy = float(y) - start[1]
    dist = math.hypot(dx, dy)
    if dist < 0.5:
        return start

    steps = min(max_steps, max(1, int(math.ceil(dist / max_step))))
    prev_x, prev_y = start
    for i in range(1, steps + 1):
        t = i / steps
        nx = start[0] + dx * t
        ny = start[1] + dy * t
        move(nx, ny, dx=nx - prev_x, dy=ny - prev_y)
        prev_x, prev_y = nx, ny
    return (float(x), float(y))


def click(x: float, y: float, button: str = "left", pause_s: float = 0.01) -> None:
    """Move to (x, y) and perform a button down/up click."""
    if button not in _BUTTON_MAP:
        raise ValueError(f"unsupported button: {button}")

    down_type, up_type, mouse_button = _BUTTON_MAP[button]
    point = _point(x, y)

    move(x, y)
    time.sleep(pause_s)

    _post(CGEventCreateMouseEvent(None, down_type, point, mouse_button))
    time.sleep(pause_s)
    _post(CGEventCreateMouseEvent(None, up_type, point, mouse_button))


def scroll(x: float, y: float, dx: int = 0, dy: int = 0) -> None:
    """Scroll at (x, y). Positive dy scrolls up (content moves down); negative dy scrolls down.

    Wheel units are line-based. `dx` is horizontal (positive = content left / view right).
    """
    move(x, y)
    time.sleep(0.01)

    wheel1 = int(dy)  # vertical
    wheel2 = int(dx)  # horizontal
    if wheel1 == 0 and wheel2 == 0:
        return

    event = CGEventCreateScrollWheelEvent(
        None,
        kCGScrollEventUnitLine,
        2,
        wheel1,
        wheel2,
    )
    _post(event)
