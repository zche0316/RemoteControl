"""Menu-bar template icon from the Squidly loader head.

Source: SquidlyApp/WebView/loader.js body path — fill-rule evenodd so the
two eye loops punch holes. Pupils and animated tentacles are dropped;
at 18pt they would turn to noise.
"""

from __future__ import annotations

import re
from typing import Iterator

from AppKit import NSBezierPath, NSColor, NSEvenOddWindingRule, NSImage
from Foundation import NSMakePoint, NSMakeSize

# Head + two circular eye holes. viewBox of the original SVG is 0 0 195.4 230.
_HEAD_PATH = (
    "M54.7,135.6l23.4,8h29.8l29.3-3.1c0,0,5.5-7.6,6.9-11.5c1.4-4,1.3-8.9,1.9-11.7"
    "c7,1,13.6-4.1,12.7-10.4c-7.8-60.3-39.9-88-54.1-97.6c-4-2.7-9.7-2.7-13.7,0"
    "c-14.2,9.5-46.2,37.2-54.1,97.1c-0.8,6.4,5.8,11.5,12.9,10.4c0.5,3.1,1,7.6,0.5,12.2"
    "L54.7,135.6z"
    "M114.5,128.9c-6.6,0-12.5-3.1-16.3-7.8c-1.3-1.6-3.8-1.6-5.2,0c-3.8,4.8-9.7,7.8-16.3,7.8"
    "c-12,0-21.7-10.1-21-22.2c0.6-10.9,9.7-19.6,20.6-19.8c6.8-0.1,12.8,3,16.7,7.8"
    "c1.3,1.6,3.8,1.6,5.2,0c3.9-4.9,10-7.9,16.7-7.8c10.9,0.2,20,9,20.6,19.8"
    "C136.1,118.8,126.5,128.9,114.5,128.9z"
)

# Crop to the head with a little padding (original y grows downward).
_VB_X, _VB_Y, _VB_W, _VB_H = 38.0, 6.0, 120.0, 146.0

_TOKEN = re.compile(r"[MmLlHhVvCcZz]|[+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?")

_ICON_PT = 18.0


def _tokens(d: str) -> Iterator[str]:
    for match in _TOKEN.finditer(d):
        yield match.group(0)


def _head_path(dest_w: float, dest_h: float, pad: float) -> NSBezierPath:
    sx = (dest_w - 2 * pad) / _VB_W
    sy = (dest_h - 2 * pad) / _VB_H

    def tx(x: float, y: float) -> tuple[float, float]:
        return pad + (x - _VB_X) * sx, pad + (y - _VB_Y) * sy

    path = NSBezierPath.bezierPath()
    path.setWindingRule_(NSEvenOddWindingRule)

    tokens = list(_tokens(_HEAD_PATH))
    i = 0
    cx = cy = 0.0
    sx0 = sy0 = 0.0
    cmd = ""

    def take(n: int) -> list[float]:
        nonlocal i
        out = [float(tokens[i + k]) for k in range(n)]
        i += n
        return out

    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                path.closePath()
                cx, cy = sx0, sy0
            continue

        if cmd == "M":
            cx, cy = take(2)
            x, y = tx(cx, cy)
            path.moveToPoint_(NSMakePoint(x, y))
            sx0, sy0 = cx, cy
            cmd = "L"
        elif cmd == "L":
            cx, cy = take(2)
            x, y = tx(cx, cy)
            path.lineToPoint_(NSMakePoint(x, y))
        elif cmd == "l":
            dx, dy = take(2)
            cx += dx
            cy += dy
            x, y = tx(cx, cy)
            path.lineToPoint_(NSMakePoint(x, y))
        elif cmd == "H":
            (cx,) = take(1)
            x, y = tx(cx, cy)
            path.lineToPoint_(NSMakePoint(x, y))
        elif cmd == "h":
            (dx,) = take(1)
            cx += dx
            x, y = tx(cx, cy)
            path.lineToPoint_(NSMakePoint(x, y))
        elif cmd == "C":
            x1, y1, x2, y2, cx, cy = take(6)
            c1 = NSMakePoint(*tx(x1, y1))
            c2 = NSMakePoint(*tx(x2, y2))
            end = NSMakePoint(*tx(cx, cy))
            path.curveToPoint_controlPoint1_controlPoint2_(end, c1, c2)
        elif cmd == "c":
            dx1, dy1, dx2, dy2, dx, dy = take(6)
            x1, y1 = cx + dx1, cy + dy1
            x2, y2 = cx + dx2, cy + dy2
            cx, cy = cx + dx, cy + dy
            c1 = NSMakePoint(*tx(x1, y1))
            c2 = NSMakePoint(*tx(x2, y2))
            end = NSMakePoint(*tx(cx, cy))
            path.curveToPoint_controlPoint1_controlPoint2_(end, c1, c2)
        else:
            raise ValueError(f"unsupported SVG command: {cmd!r}")

    return path


def make_template_image() -> NSImage:
    """18pt template image; AppKit draws a 2x bitmap on retina."""
    size = NSMakeSize(_ICON_PT, _ICON_PT)
    image = NSImage.alloc().initWithSize_(size)
    image.lockFocusFlipped_(True)
    NSColor.blackColor().setFill()
    _head_path(_ICON_PT, _ICON_PT, pad=1.25).fill()
    image.unlockFocus()
    image.setTemplate_(True)
    image.setSize_(size)
    return image
