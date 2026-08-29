#!/usr/bin/env python3
"""Build packaging/icon/RemoteAgent.icns from the Squidly loader geometry.

Source: SquidlyApp/WebView/loader.js — head, pupils, and tentacles at t=0.5
(keyframe 2: the mid bounce, --y = 0). Fill #8F53C9 on a transparent canvas
so Finder does not show a white square around the squid.

Run from the repo root or this directory:
    python3 packaging/icon/make_icon.py
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

# Cubics for each tentacle between 5 poses (loader.js LURPS). Index 2 = t=0.5.
LURPS = [
    [
        [
            [[50.24, 128.4], [44.28, 151.8], [7.245, 159.7], [13.25, 190.6]],
            [[50.58, 127.1], [47.17, 152.2], [16.54, 164.9], [19.8, 193.2]],
            [[50.8, 126.4], [47.34, 151.1], [26.95, 169.7], [26.54, 195.5]],
            [[51.2, 125.4], [45.07, 149.5], [37.23, 173.2], [33.26, 197.9]],
            [[51.75, 124.2], [40.79, 148.5], [46.47, 175.5], [39.55, 200.6]],
        ],
        [
            [[25.66, 191.7], [33.12, 160.4], [79.15, 170.1], [84.76, 136.2]],
            [[32.11, 195.2], [40.21, 167.5], [76.67, 165.9], [84.42, 137.5]],
            [[38.54, 198.8], [47.77, 174.1], [74.25, 162.5], [84.2, 138.2]],
            [[44.88, 202.4], [56.08, 180.2], [70.72, 160.2], [83.8, 139.2]],
            [[51.09, 205.3], [65.17, 185.2], [66.79, 159.1], [83.25, 140.4]],
        ],
    ],
    [
        [
            [[79.11, 140.3], [97.68, 153.6], [92.92, 178.6], [71.59, 185.8]],
            [[79.22, 140.5], [96.86, 154.4], [92.09, 178.9], [75.05, 190.9]],
            [[79.33, 140.7], [97.26, 155.8], [89.2, 179.9], [78.66, 196.7]],
            [[79.43, 140.9], [98.09, 157.6], [85.16, 182.5], [82.64, 203]],
            [[79.52, 141.1], [98.76, 159.8], [80.83, 186.9], [86.98, 209.7]],
        ],
        [
            [[74.88, 197.9], [111, 195.4], [129.9, 154.3], [110.7, 124.3]],
            [[81.64, 201.5], [112.8, 190.4], [127.2, 153], [110.6, 124.1]],
            [[88.12, 204.8], [111.7, 185.7], [126.3, 152.6], [110.5, 123.9]],
            [[94.15, 207.8], [108.6, 182.5], [126.7, 152.8], [110.4, 123.7]],
            [[99.42, 210.4], [104.4, 181.3], [128, 153.3], [110.3, 123.5]],
        ],
    ],
    [
        [
            [[110.3, 117.8], [99.61, 165.4], [157.2, 159.7], [169.3, 192.4]],
            [[110.1, 119.9], [105.5, 159.1], [149.7, 165.6], [160.4, 196]],
            [[110.2, 122], [112, 154.3], [141.6, 171.3], [151.5, 199.6]],
            [[110.5, 124.2], [119.5, 151.4], [132.6, 176.9], [142.4, 203.8]],
            [[111.1, 126.4], [127, 151], [122.3, 181.2], [133.5, 207.2]],
        ],
        [
            [[181.5, 189.8], [188.9, 155.3], [134.6, 154.6], [145.3, 123.4]],
            [[172.6, 193.5], [177, 163], [135.9, 151.6], [145.5, 121.3]],
            [[163.8, 197.2], [164.4, 169.1], [139.7, 147.9], [145.4, 119.2]],
            [[154.4, 200.4], [152.3, 172.5], [146.5, 145], [145.1, 117]],
            [[145.6, 204], [142.7, 174.2], [154.1, 144.4], [144.5, 114.8]],
        ],
    ],
]

HEAD = (
    "M54.7,135.6l23.4,8h29.8l29.3-3.1c0,0,5.5-7.6,6.9-11.5c1.4-4,1.3-8.9,1.9-11.7"
    "c7,1,13.6-4.1,12.7-10.4c-7.8-60.3-39.9-88-54.1-97.6c-4-2.7-9.7-2.7-13.7,0"
    "c-14.2,9.5-46.2,37.2-54.1,97.1c-0.8,6.4,5.8,11.5,12.9,10.4c0.5,3.1,1,7.6,0.5,12.2"
    "L54.7,135.6z"
    "M114.5,128.9c-6.6,0-12.5-3.1-16.3-7.8c-1.3-1.6-3.8-1.6-5.2,0c-3.8,4.8-9.7,7.8-16.3,7.8"
    "c-12,0-21.7-10.1-21-22.2c0.6-10.9,9.7-19.6,20.6-19.8c6.8-0.1,12.8,3,16.7,7.8"
    "c1.3,1.6,3.8,1.6,5.2,0c3.9-4.9,10-7.9,16.7-7.8c10.9,0.2,20,9,20.6,19.8"
    "C136.1,118.8,126.5,128.9,114.5,128.9z"
)
PUPIL_L = (
    "M82,109.8c-2.2-0.4-3.9-2.2-4.2-4.4c-0.3-1.8,0.3-3.6,1.4-4.7c1-1,0.3-2.7-1.1-2.9"
    "c-0.9-0.1-1.9-0.1-2.9,0c-4.6,0.6-8.3,4.4-8.8,9.1c-0.6,6.1,4.2,11.3,10.2,11.3"
    "c4.1,0,7.6-2.4,9.2-5.8c0.6-1.3-0.4-2.7-1.9-2.5C83.4,109.9,82.7,109.9,82,109.8z"
)
PUPIL_R = (
    "M120.2,109.8c-2.2-0.4-3.9-2.2-4.2-4.4c-0.3-1.8,0.3-3.6,1.4-4.7c1-1,0.3-2.7-1.1-2.9"
    "c-0.9-0.1-1.9-0.1-2.9,0c-4.6,0.6-8.3,4.4-8.8,9.1c-0.6,6.1,4.2,11.3,10.2,11.3"
    "c4.1,0,7.6-2.4,9.2-5.8c0.6-1.3-0.4-2.7-1.9-2.5C121.6,109.9,120.9,109.9,120.2,109.8z"
)

FILL = "#8F53C9"
# Loader viewBox 0 0 195.4 230, fitted into a 1024 square with ~12% inset
# so Finder's squircle mask does not clip tentacles.
CANVAS = 1024
VB_W, VB_H = 195.4, 230.0
INSET = 0.12
ICONSET_SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def _pt(p: list[float]) -> str:
    return f"{p[0]},{p[1]}"


def _cubic(pts: list[list[float]]) -> str:
    rest = " ".join(_pt(p) for p in pts[1:])
    return f"M{_pt(pts[0])}C{rest}"


def tentacle_d(left: list[list[float]], right: list[list[float]]) -> str:
    # loader.js: M l[0] C l[1..] A6,6,0,0,0 r[0] C r[1..]
    return (
        f"{_cubic(left)}A6,6,0,0,0,{_pt(right[0])}"
        f"C{' '.join(_pt(p) for p in right[1:])}"
    )


def squid_svg() -> str:
    pose = 2
    tentacles = "\n    ".join(
        f'<path d="{tentacle_d(arm[0][pose], arm[1][pose])}"/>' for arm in LURPS
    )
    avail = CANVAS * (1 - 2 * INSET)
    scale = min(avail / VB_W, avail / VB_H)
    tw, th = VB_W * scale, VB_H * scale
    tx, ty = (CANVAS - tw) / 2, (CANVAS - th) / 2
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS}" height="{CANVAS}" viewBox="0 0 {CANVAS} {CANVAS}">
  <g fill="{FILL}" transform="translate({tx:.3f} {ty:.3f}) scale({scale:.6f})">
    {tentacles}
    <rect fill="#ffffff" x="53.4" y="79.3" width="87.2" height="51.3"/>
    <path fill-rule="evenodd" d="{HEAD}"/>
    <path d="{PUPIL_L}"/>
    <path d="{PUPIL_R}"/>
  </g>
</svg>
"""


def main() -> None:
    here = Path(__file__).resolve().parent
    svg_path = here / "squidly-loader.svg"
    icns_path = here / "RemoteAgent.icns"
    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        raise SystemExit("need ImageMagick (magick) to rasterize the SVG")
    svg_path.write_text(squid_svg(), encoding="utf-8")
    print(f"wrote {svg_path}")

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "RemoteAgent.iconset"
        iconset.mkdir()
        master = Path(tmp) / "master.png"
        subprocess.run(
            [
                magick,
                "-background",
                "none",
                str(svg_path),
                f"PNG32:{master}",
            ],
            check=True,
        )
        for name, size in ICONSET_SIZES.items():
            dest = iconset / name
            subprocess.run(
                [
                    magick,
                    str(master),
                    "-background",
                    "none",
                    "-filter",
                    "Lanczos",
                    "-resize",
                    f"{size}x{size}",
                    f"PNG32:{dest}",
                ],
                check=True,
            )
        subprocess.run(
            ["iconutil", "-c", "icns", "-o", str(icns_path), str(iconset)],
            check=True,
        )
    print(f"wrote {icns_path}")


if __name__ == "__main__":
    main()
