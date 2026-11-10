"""Synthetic mouse events via CoreGraphics CGEventPost."""

from __future__ import annotations

import math
import time

from AppKit import NSApplication, NSWindow, NSWorkspace
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCopyElementAtPosition,
    AXUIElementCreateApplication,
    AXUIElementCreateSystemWide,
    kAXFocusedWindowAttribute,
    kAXWindowAttribute,
)
from CoreFoundation import CFEqual
from Quartz import (
    CGDisplayBounds,
    CGEventCreate,
    CGEventCreateMouseEvent,
    CGEventCreateScrollWheelEvent,
    CGEventGetLocation,
    CGEventPost,
    CGEventSetIntegerValueField,
    CGGetActiveDisplayList,
    CGMainDisplayID,
    CGPointMake,
    CGWindowListCopyWindowInfo,
    kCGNullWindowID,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionOnScreenOnly,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseDragged,
    kCGEventLeftMouseUp,
    kCGEventOtherMouseDown,
    kCGEventOtherMouseDragged,
    kCGEventOtherMouseUp,
    kCGEventRightMouseDown,
    kCGEventRightMouseDragged,
    kCGEventRightMouseUp,
    kCGEventMouseMoved,
    kCGHIDEventTap,
    kCGMouseButtonCenter,
    kCGMouseButtonLeft,
    kCGMouseButtonRight,
    kCGMouseEventClickState,
    kCGMouseEventDeltaX,
    kCGMouseEventDeltaY,
    kCGScrollEventUnitLine,
)

# (down_type, up_type, drag_type, button_constant)
_BUTTON_MAP = {
    "left": (
        kCGEventLeftMouseDown,
        kCGEventLeftMouseUp,
        kCGEventLeftMouseDragged,
        kCGMouseButtonLeft,
    ),
    "right": (
        kCGEventRightMouseDown,
        kCGEventRightMouseUp,
        kCGEventRightMouseDragged,
        kCGMouseButtonRight,
    ),
    "middle": (
        kCGEventOtherMouseDown,
        kCGEventOtherMouseUp,
        kCGEventOtherMouseDragged,
        kCGMouseButtonCenter,
    ),
}


def _point(x: float, y: float):
    return CGPointMake(float(x), float(y))


def _post(event) -> None:
    if event is None:
        raise RuntimeError("failed to create CGEvent")
    CGEventPost(kCGHIDEventTap, event)


def _quartz_rect_from_cg(bounds) -> dict[str, float]:
    return {
        "x": float(bounds.origin.x),
        "y": float(bounds.origin.y),
        "width": float(bounds.size.width),
        "height": float(bounds.size.height),
    }


def main_display_bounds() -> dict[str, float]:
    """Primary display bounds in CoreGraphics global coordinates (top-left origin)."""
    return _quartz_rect_from_cg(CGDisplayBounds(CGMainDisplayID()))


def list_displays() -> list[dict[str, float | int]]:
    max_displays = 16
    result = CGGetActiveDisplayList(max_displays, None, None)
    ids: list[int] = []
    if isinstance(result, tuple) and len(result) >= 3:
        err, displays, count = result[0], result[1], result[2]
        if not err and displays is not None:
            ids = [int(displays[i]) for i in range(int(count))]
    if not ids:
        ids = [int(CGMainDisplayID())]
    out: list[dict[str, float | int]] = []
    for display_id in ids:
        rect = _quartz_rect_from_cg(CGDisplayBounds(display_id))
        out.append({"id": int(display_id), **rect})
    return out


def list_windows() -> list[dict[str, float | str | int]]:
    """On-screen layer-0 windows. kCGWindowBounds is already Quartz top-left."""
    options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    raw = CGWindowListCopyWindowInfo(options, kCGNullWindowID) or []
    out: list[dict[str, float | str | int]] = []
    for info in raw:
        try:
            layer = int(info.get("kCGWindowLayer", 1))
            alpha = float(info.get("kCGWindowAlpha", 1))
            bounds = info.get("kCGWindowBounds") or {}
            width = float(bounds.get("Width", 0))
            height = float(bounds.get("Height", 0))
            x = float(bounds.get("X", 0))
            y = float(bounds.get("Y", 0))
        except (TypeError, ValueError, AttributeError):
            continue
        if layer != 0 or alpha < 0.5 or width <= 0 or height <= 0:
            continue
        out.append(
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "name": str(info.get("kCGWindowName") or ""),
                "owner": str(info.get("kCGWindowOwnerName") or ""),
                "owner_pid": int(info.get("kCGWindowOwnerPID") or 0),
                "id": int(info.get("kCGWindowNumber") or 0),
            }
        )
    return out


def match_captured_surface(surface: str, video_width: float, video_height: float, surface_id: int | None = None) -> dict | None:
    """Resolve an explicitly selected native ID, independent of capture size."""
    if surface_id is None:
        return None
    kind = (surface or "window").lower()
    if kind == "monitor":
        items = [{"kind": "monitor", **d} for d in list_displays()]
    else:
        items = [{"kind": "window", **w} for w in list_windows()]
    if surface_id is not None:
        items = [item for item in items if item.get("id") == surface_id]
    # Once identified, the window ID remains stable across moves and resizes.
    best = dict(items[0]) if len(items) == 1 else None
    if not best:
        return None
    return {
        "x": float(best["x"]),
        "y": float(best["y"]),
        "width": float(best["width"]),
        "height": float(best["height"]),
        "kind": str(best.get("kind") or kind),
        "name": str(best.get("name") or ""),
        "owner": str(best.get("owner") or ""),
        "id": int(best.get("id") or 0),
    }


class WindowHitMismatch(Exception):
    """The shared window would not receive an event at the requested point."""


def window_relative_point(bounds: dict, nx: float, ny: float) -> tuple[float, float]:
    """Map a normalized point to the interior of a Quartz window rectangle."""
    x = float(bounds.get("X", 0))
    y = float(bounds.get("Y", 0))
    width = float(bounds.get("Width", 0))
    height = float(bounds.get("Height", 0))

    # Quartz rectangles are half-open on their right and bottom edges. Keep
    # normalized edge coordinates inside the target window so nx/ny == 1 does
    # not hit the desktop or an adjacent window.
    inset_x = min(0.5, width / 2)
    inset_y = min(0.5, height / 2)
    px = min(max(float(nx) * width, inset_x), max(inset_x, width - inset_x))
    py = min(max(float(ny) * height, inset_y), max(inset_y, height - inset_y))
    return (x + px, y + py)


_input_transparent_windows: frozenset[int] = frozenset()


def set_input_transparent_windows(window_ids) -> None:
    """Register only our own green panels, which explicitly ignore mouse input."""
    global _input_transparent_windows
    _input_transparent_windows = frozenset(int(value) for value in window_ids)


def window_hit_at(x: float, y: float) -> int:
    """Ask WindowServer which window would receive a mouse-down here.

    CoreGraphics uses a top-left origin; AppKit uses a bottom-left origin.
    A returned zero or an AppKit failure is treated as an unknown hit.
    """
    try:
        NSApplication.sharedApplication()
        main = CGDisplayBounds(CGMainDisplayID())
        point = (float(x), float(main.size.height) - float(y))
        below = 0
        transparent = _input_transparent_windows
        for _ in range(len(transparent) + 1):
            hit = int(NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_(point, below))
            if hit not in transparent:
                return hit
            if hit == below:
                return 0
            below = hit
        return 0
    except Exception:
        return 0


def require_window_hit(window_id: int, x: float, y: float) -> None:
    if window_hit_at(x, y) != window_id:
        raise WindowHitMismatch(window_id)


def window_control_state(window_id: int, nx: float = 0.5, ny: float = 0.5) -> tuple[str, dict | None]:
    """Track a shared window by ID and test the current control point."""
    options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) or []
    target_index = next((i for i, w in enumerate(windows) if int(w.get("kCGWindowNumber") or 0) == window_id), None)
    if target_index is None:
        return ("window_unavailable", None)
    target = windows[target_index]
    bounds = target.get("kCGWindowBounds") or {}
    if (int(target.get("kCGWindowLayer", 0)) != 0 or float(target.get("kCGWindowAlpha", 1)) < 0.5
            or float(bounds.get("Width", 0)) <= 0 or float(bounds.get("Height", 0)) <= 0):
        return ("window_unavailable", None)
    x, y = window_relative_point(bounds, nx, ny)
    if window_hit_at(x, y) != window_id:
        return ("window_obscured", bounds)
    return ("ready", bounds)


def window_keyboard_focus_state(window_id: int, nx: float, ny: float) -> str:
    """Keyboard events must go to the shared window, not another window of its app."""
    state, bounds = window_control_state(window_id, nx, ny)
    if state != "ready" or bounds is None:
        return state
    try:
        options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) or []
        target = next(w for w in windows if int(w.get("kCGWindowNumber") or 0) == window_id)
        pid = int(target.get("kCGWindowOwnerPID") or 0)
        front_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if not pid or front_app is None or int(front_app.processIdentifier()) != pid:
            return "window_not_frontmost"

        x, y = window_relative_point(bounds, nx, ny)
        err, element = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), x, y, None)
        if err or element is None:
            return "window_focus_unavailable"
        err, hit_window = AXUIElementCopyAttributeValue(element, kAXWindowAttribute, None)
        if err or hit_window is None:
            return "window_focus_unavailable"
        err, focused_window = AXUIElementCopyAttributeValue(
            AXUIElementCreateApplication(pid), kAXFocusedWindowAttribute, None)
        if err or focused_window is None:
            return "window_focus_unavailable"
        return "ready" if CFEqual(hit_window, focused_window) else "window_not_frontmost"
    except Exception:
        return "window_focus_unavailable"


def window_point(window_id: int, nx: float, ny: float) -> tuple[float, float] | None:
    """Resolve a point only while the entire shared window is controllable."""
    if not (0 <= nx <= 1 and 0 <= ny <= 1):
        return None
    state, bounds = window_control_state(window_id, nx, ny)
    if state != "ready" or bounds is None:
        return None
    return window_relative_point(bounds, nx, ny)


def window_is_frontmost(window_id: int) -> bool:
    windows = list_windows()
    return bool(windows and windows[0]["id"] == window_id)


def current_position() -> tuple[float, float]:
    event = CGEventCreate(None)
    if event is None:
        return (0.0, 0.0)
    loc = CGEventGetLocation(event)
    return (float(loc.x), float(loc.y))


def move(
    x: float,
    y: float,
    dx: float | None = None,
    dy: float | None = None,
    drag_button: str | None = None,
    window_id: int | None = None,
) -> None:
    """Move the cursor to absolute screen coordinates (top-left origin).

    When `drag_button` is set, posts a dragged event for that button instead of MouseMoved.
    """
    if drag_button is not None:
        if drag_button not in _BUTTON_MAP:
            raise ValueError(f"unsupported button: {drag_button}")
        _down, _up, event_type, mouse_button = _BUTTON_MAP[drag_button]
    else:
        event_type = kCGEventMouseMoved
        mouse_button = kCGMouseButtonLeft

    if window_id is not None:
        require_window_hit(window_id, x, y)
    point = _point(x, y)
    event = CGEventCreateMouseEvent(None, event_type, point, mouse_button)
    if dx is not None:
        CGEventSetIntegerValueField(event, kCGMouseEventDeltaX, int(round(dx)))
    if dy is not None:
        CGEventSetIntegerValueField(event, kCGMouseEventDeltaY, int(round(dy)))
    _post(event)


def _post_button(event_type, x: float, y: float, mouse_button, click_state: int = 1) -> None:
    event = CGEventCreateMouseEvent(None, event_type, _point(x, y), mouse_button)
    CGEventSetIntegerValueField(event, kCGMouseEventClickState, int(click_state))
    _post(event)


def move_interpolated(
    x: float,
    y: float,
    origin: tuple[float, float] | None = None,
    max_step: float = 8.0,
    max_steps: int = 48,
    drag_button: str | None = None,
    window_id: int | None = None,
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
        move(nx, ny, dx=nx - prev_x, dy=ny - prev_y,
             drag_button=drag_button, window_id=window_id)
        prev_x, prev_y = nx, ny
    return (float(x), float(y))


def down(x: float, y: float, button: str = "left", window_id: int | None = None) -> None:
    """Move to (x, y) and press a mouse button without releasing it."""
    if button not in _BUTTON_MAP:
        raise ValueError(f"unsupported button: {button}")
    down_type, _up, _drag, mouse_button = _BUTTON_MAP[button]
    move(x, y, window_id=window_id)
    time.sleep(0.01)
    if window_id is not None:
        require_window_hit(window_id, x, y)
    _post_button(down_type, x, y, mouse_button)


def up(x: float, y: float, button: str = "left", window_id: int | None = None) -> None:
    """Move to (x, y) and release a mouse button."""
    if button not in _BUTTON_MAP:
        raise ValueError(f"unsupported button: {button}")
    _down, up_type, _drag, mouse_button = _BUTTON_MAP[button]
    move_interpolated(x, y, drag_button=button, window_id=window_id)
    time.sleep(0.01)
    if window_id is not None:
        require_window_hit(window_id, x, y)
    _post_button(up_type, x, y, mouse_button)


def release_button(button: str) -> None:
    """End a held drag without moving the pointer to an obscured target."""
    if button not in _BUTTON_MAP:
        raise ValueError(f"unsupported button: {button}")
    _down, up_type, _drag, mouse_button = _BUTTON_MAP[button]
    x, y = current_position()
    _post_button(up_type, x, y, mouse_button)


def click(x: float, y: float, button: str = "left", pause_s: float = 0.01,
          window_id: int | None = None, clicks: int = 1) -> None:
    """Move to (x, y) and perform a button down/up click."""
    if button not in _BUTTON_MAP:
        raise ValueError(f"unsupported button: {button}")

    down_type, up_type, _drag, mouse_button = _BUTTON_MAP[button]

    move(x, y, window_id=window_id)
    time.sleep(pause_s)
    if window_id is not None:
        require_window_hit(window_id, x, y)
    _post_button(down_type, x, y, mouse_button, click_state=clicks)
    try:
        time.sleep(pause_s)
        if window_id is not None:
            require_window_hit(window_id, x, y)
    except BaseException:
        release_button(button)
        raise
    _post_button(up_type, x, y, mouse_button, click_state=clicks)


def scroll(x: float, y: float, dx: int = 0, dy: int = 0,
           window_id: int | None = None) -> None:
    """Scroll at (x, y). Positive dy scrolls up (content moves down); negative dy scrolls down.

    Wheel units are line-based. `dx` is horizontal (positive = content left / view right).
    """
    move(x, y, window_id=window_id)
    time.sleep(0.01)

    wheel1 = int(dy)  # vertical
    wheel2 = int(dx)  # horizontal
    if wheel1 == 0 and wheel2 == 0:
        return

    if window_id is not None:
        require_window_hit(window_id, x, y)

    event = CGEventCreateScrollWheelEvent(
        None,
        kCGScrollEventUnitLine,
        2,
        wheel1,
        wheel2,
    )
    _post(event)
