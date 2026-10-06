"""Menu-bar template icon restored from ``packaging/icon/logo.svg``.

The complete silhouette is retained: the three bottom legs are a recognisable
part of the Squidly mark. The even-odd fill rule leaves its eye loops clear.
"""

from __future__ import annotations

import re
from typing import Iterator

from AppKit import NSBezierPath, NSColor, NSEvenOddWindingRule, NSImage
from Foundation import NSMakePoint, NSMakeSize

# Complete body, including the two side legs and centre leg. This is copied
# from the first path of packaging/icon/logo.svg; the next subpath is the eyes.
_LOGO_PATH = (
    "M181.7,192.1c-.7-9.7-2.5-35.3-26-45.5-12.8-5.5-11.2-22.3-9.8-29.4,7,1,13.6-4.1,12.7-10.4-7.8-60.3-39.9-88-54.1-97.6-4-2.7-9.7-2.7-13.7,0-14.2,9.5-46.2,37.2-54.1,97.1-.8,6.4,5.8,11.5,12.9,10.4,1.3,7.5,2.3,23.3-10,28.6-23.5,10.2-25.3,35.8-26,45.5,0,.6-.1,1.1-.1,1.5-.2,3,1.8,5.7,4.7,6.3.4.1.8.1,1.3.1,2.5,0,4.8-1.6,5.6-4,.1-.2,8.6-23.4,27.8-26.2,13.2-1.9,22.3-7.9,28.5-16,1.1,2.1,2.4,4.2,4.1,6.1,2.8,3.1,2.6,5,2.6,5.6-.4,5-8.1,11.2-14.4,16.2-1.7,1.4-3.5,2.8-5.1,4.2-2,1.7-2.7,4.6-1.6,7,1,2.2,3.1,3.6,5.5,3.6h.8c.4,0,9.7-1.3,19.7-4.5,14.8-4.7,23.4-11.5,25.6-19.9,1.1-4,1.4-7.5,1.3-10.6,5.5,4.8,12.7,8.3,22.3,9.8,19.2,2.8,27.7,26,27.8,26.2.9,2.4,3.2,4,5.7,4s.8,0,1.3-.1c2.9-.6,5-3.3,4.7-6.4.1-.5.1-1,0-1.6h0ZM114.5,128.9c-6.6,0-12.5-3.1-16.3-7.8-1.3-1.6-3.8-1.6-5.2,0-3.8,4.8-9.7,7.8-16.3,7.8-12,0-21.7-10.1-21-22.2.6-10.9,9.7-19.6,20.6-19.8,6.8,0,12.8,3,16.7,7.8,1.3,1.6,3.8,1.6,5.2,0,3.9-4.9,10-7.9,16.7-7.8,10.9.2,20,9,20.6,19.8.6,12.1-9,22.2-21,22.2Z"
    "M81.76,110.035c-2.558-.873-4.306-3.387-4.248-6.089.031-1.441.521-2.729,1.314-3.755.787-1.019.093-2.538-1.189-2.652-1.035-.093-2.113-.03-3.211.215-4.436.991-7.77,4.895-8.056,9.432-.392,6.239,4.67,11.293,10.755,11.058,3.775-.145,7.002-2.29,8.699-5.376.65-1.182-.279-2.565-1.626-2.491-.793.044-1.616-.062-2.438-.342Z"
    "M119.931,110.035c-2.558-.873-4.306-3.387-4.248-6.089.031-1.441.521-2.729,1.314-3.755.787-1.019.093-2.538-1.189-2.652-1.035-.093-2.113-.03-3.211.215-4.436.991-7.77,4.895-8.056,9.432-.392,6.239,4.67,11.293,10.755,11.058,3.775-.145,7.002-2.29,8.699-5.376.65-1.182-.279-2.565-1.626-2.491-.793.044-1.616-.062-2.438-.342Z"

#    "M181.7,192.1c-.7-9.7-2.5-35.3-26-45.5c-12.8-5.5-11.2-22.3-9.8-29.4c7,1,13.6-4.1,12.7-10.4"
 #   "c-7.8-60.3-39.9-88-54.1-97.6c-4-2.7-9.7-2.7-13.7,0c-14.2,9.5-46.2,37.2-54.1,97.1"
 #   "c-.8,6.4,5.8,11.5,12.9,10.4c1.3,7.5,2.3,23.3-10,28.6c-23.5,10.2-25.3,35.8-26,45.5"
  #  "c0,.6-.1,1.1-.1,1.5c-.2,3,1.8,5.7,4.7,6.3c.4.1,.8.1,1.3.1c2.5,0,4.8-1.6,5.6-4"
   # "c.1-.2,8.6-23.4,27.8-26.2c13.2-1.9,22.3-7.9,28.5-16c1.1,2.1,2.4,4.2,4.1,6.1"
   # "c2.8,3.1,2.6,5,2.6,5.6c-.4,5-8.1,11.2-14.4,16.2c-1.7,1.4-3.5,2.8-5.1,4.2"
  #  "c-2,1.7-2.7,4.6-1.6,7c1,2.2,3.1,3.6,5.5,3.6h.8c.4,0,9.7-1.3,19.7-4.5"
   # "c14.8-4.7,23.4-11.5,25.6-19.9c1.1-4,1.4-7.5,1.3-10.6c5.5,4.8,12.7,8.3,22.3,9.8"
   # "c19.2,2.8,27.7,26,27.8,26.2c.9,2.4,3.2,4,5.7,4s.8,0,1.3-.1c2.9-.6,5-3.3,4.7-6.4"
   # "c.1-.5.1-1,0-1.6h0Z"
   # "M114.5,128.9c-6.6,0-12.5-3.1-16.3-7.8c-1.3-1.6-3.8-1.6-5.2,0c-3.8,4.8-9.7,7.8-16.3,7.8"
   # "c-12,0-21.7-10.1-21-22.2c0.6-10.9,9.7-19.6,20.6-19.8c6.8-0.1,12.8,3,16.7,7.8"
   # "c1.3,1.6,3.8,1.6,5.2,0c3.9-4.9,10-7.9,16.7-7.8c10.9,0.2,20,9,20.6,19.8"
   # "C136.1,118.8,126.5,128.9,114.5,128.9z"
)

# Original SVG viewBox; use uniform scaling to avoid distorting the mark.
_VB_X, _VB_Y, _VB_W, _VB_H = 0.0, 0.0, 195.4, 207.3

_TOKEN = re.compile(r"[MmLlHhVvCcSsZz]|[+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?")

_ICON_PT = 18.0


def _tokens(d: str) -> Iterator[str]:
    for match in _TOKEN.finditer(d):
        yield match.group(0)


def _logo_path(dest_w: float, dest_h: float, pad: float) -> NSBezierPath:
    scale = min((dest_w - 2 * pad) / _VB_W, (dest_h - 2 * pad) / _VB_H)
    offset_x = (dest_w - _VB_W * scale) / 2
    offset_y = (dest_h - _VB_H * scale) / 2

    def tx(x: float, y: float) -> tuple[float, float]:
        return offset_x + (x - _VB_X) * scale, offset_y + (y - _VB_Y) * scale

    path = NSBezierPath.bezierPath()
    path.setWindingRule_(NSEvenOddWindingRule)

    tokens = list(_tokens(_LOGO_PATH))
    i = 0
    cx = cy = 0.0
    sx0 = sy0 = 0.0
    last_cubic_control: tuple[float, float] | None = None
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
            last_cubic_control = None
            cmd = "L"
        elif cmd == "L":
            cx, cy = take(2)
            x, y = tx(cx, cy)
            path.lineToPoint_(NSMakePoint(x, y))
            last_cubic_control = None
        elif cmd == "l":
            dx, dy = take(2)
            cx += dx
            cy += dy
            x, y = tx(cx, cy)
            path.lineToPoint_(NSMakePoint(x, y))
            last_cubic_control = None
        elif cmd == "H":
            (cx,) = take(1)
            x, y = tx(cx, cy)
            path.lineToPoint_(NSMakePoint(x, y))
            last_cubic_control = None
        elif cmd == "h":
            (dx,) = take(1)
            cx += dx
            x, y = tx(cx, cy)
            path.lineToPoint_(NSMakePoint(x, y))
            last_cubic_control = None
        elif cmd == "C":
            x1, y1, x2, y2, cx, cy = take(6)
            c1 = NSMakePoint(*tx(x1, y1))
            c2 = NSMakePoint(*tx(x2, y2))
            end = NSMakePoint(*tx(cx, cy))
            path.curveToPoint_controlPoint1_controlPoint2_(end, c1, c2)
            last_cubic_control = (x2, y2)
        elif cmd == "c":
            dx1, dy1, dx2, dy2, dx, dy = take(6)
            x1, y1 = cx + dx1, cy + dy1
            x2, y2 = cx + dx2, cy + dy2
            cx, cy = cx + dx, cy + dy
            c1 = NSMakePoint(*tx(x1, y1))
            c2 = NSMakePoint(*tx(x2, y2))
            end = NSMakePoint(*tx(cx, cy))
            path.curveToPoint_controlPoint1_controlPoint2_(end, c1, c2)
            last_cubic_control = (x2, y2)
        elif cmd == "S":
            x2, y2, end_x, end_y = take(4)
            start_x, start_y = cx, cy
            x1, y1 = (
                (2 * start_x - last_cubic_control[0], 2 * start_y - last_cubic_control[1])
                if last_cubic_control is not None
                else (start_x, start_y)
            )
            cx, cy = end_x, end_y
            c1 = NSMakePoint(*tx(x1, y1))
            c2 = NSMakePoint(*tx(x2, y2))
            end = NSMakePoint(*tx(cx, cy))
            path.curveToPoint_controlPoint1_controlPoint2_(end, c1, c2)
            last_cubic_control = (x2, y2)
        elif cmd == "s":
            dx2, dy2, dx, dy = take(4)
            start_x, start_y = cx, cy
            x1, y1 = (
                (2 * start_x - last_cubic_control[0], 2 * start_y - last_cubic_control[1])
                if last_cubic_control is not None
                else (start_x, start_y)
            )
            x2, y2 = start_x + dx2, start_y + dy2
            cx, cy = start_x + dx, start_y + dy
            c1 = NSMakePoint(*tx(x1, y1))
            c2 = NSMakePoint(*tx(x2, y2))
            end = NSMakePoint(*tx(cx, cy))
            path.curveToPoint_controlPoint1_controlPoint2_(end, c1, c2)
            last_cubic_control = (x2, y2)
        else:
            raise ValueError(f"unsupported SVG command: {cmd!r}")

    return path


def make_template_image() -> NSImage:
    """18pt template image; AppKit draws a 2x bitmap on retina."""
    size = NSMakeSize(_ICON_PT, _ICON_PT)
    image = NSImage.alloc().initWithSize_(size)
    image.lockFocusFlipped_(True)
    NSColor.blackColor().setFill()
    _logo_path(_ICON_PT, _ICON_PT, pad=1.25).fill()
    image.unlockFocus()
    image.setTemplate_(True)
    image.setSize_(size)
    return image
