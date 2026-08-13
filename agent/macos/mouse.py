"""Synthetic mouse events via CoreGraphics CGEventPost."""

from __future__ import annotations

import time

from Quartz import (
    CGEventCreateMouseEvent,
    CGEventCreateScrollWheelEvent,
    CGEventPost,
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


def move(x: float, y: float) -> None:
    """Move the cursor to absolute screen coordinates (top-left origin)."""
    point = _point(x, y)
    event = CGEventCreateMouseEvent(None, kCGEventMouseMoved, point, kCGMouseButtonLeft)
    _post(event)


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

    # wheelCount=2 with vertical then horizontal deltas
    event = CGEventCreateScrollWheelEvent(
        None,
        kCGScrollEventUnitLine,
        2,
        wheel1,
        wheel2,
    )
    _post(event)
