"""Generate FloodSafe's notification icons.

    python scripts/make_notification_icons.py

Writes:
    frontend/public/icons/floodsafe-192.png        full-colour notification icon
    frontend/public/icons/floodsafe-badge-96.png   monochrome status-bar badge

Why these exist: notifications previously pointed at /favicon.svg, a file that
was never created, so every alert showed the browser's generic icon. PNG rather
than SVG because Android's notification shade expects raster images, and the
badge MUST be a white silhouette on transparency - Android renders the badge
from its alpha channel alone, so any colour in it is thrown away.

Drawn from primitives here rather than taken from an icon set, so there is no
licensing question.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "public" / "icons"

# FloodSafe's accent blue, and a warning amber for the wave crest.
BG = (27, 64, 138, 255)
WHITE = (255, 255, 255, 255)
AMBER = (255, 176, 32, 255)

SUPERSAMPLE = 4  # draw large, downscale, for smooth edges without antialias support


def droplet(draw: ImageDraw.ImageDraw, size: int, fill, *, cx: float, top: float, bottom: float, width: float) -> None:
    """A water droplet: a circle with a triangle rising to a point."""
    r = width / 2
    cy = bottom - r
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    draw.polygon([(cx, top), (cx - r * 0.97, cy - r * 0.25), (cx + r * 0.97, cy - r * 0.25)], fill=fill)


def wave(draw: ImageDraw.ImageDraw, *, x0: float, x1: float, y: float, amp: float, fill, stroke: int) -> None:
    import math

    points = []
    steps = 60
    for i in range(steps + 1):
        x = x0 + (x1 - x0) * i / steps
        points.append((x, y + amp * math.sin(i / steps * 2 * math.pi * 1.5)))
    draw.line(points, fill=fill, width=stroke, joint="curve")


def make_icon(size: int) -> Image.Image:
    s = size * SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=BG)
    droplet(d, s, WHITE, cx=s * 0.5, top=s * 0.14, bottom=s * 0.74, width=s * 0.46)
    # Wave across the lower half of the droplet, in amber, reading as rising water.
    wave(d, x0=s * 0.16, x1=s * 0.84, y=s * 0.80, amp=s * 0.035, fill=AMBER, stroke=int(s * 0.055))
    wave(d, x0=s * 0.30, x1=s * 0.70, y=s * 0.60, amp=s * 0.03, fill=BG, stroke=int(s * 0.045))
    return img.resize((size, size), Image.LANCZOS)


def make_badge(size: int) -> Image.Image:
    s = size * SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    droplet(d, s, WHITE, cx=s * 0.5, top=s * 0.06, bottom=s * 0.94, width=s * 0.66)
    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, image in (
        ("floodsafe-192.png", make_icon(192)),
        ("floodsafe-badge-96.png", make_badge(96)),
    ):
        path = OUT / name
        image.save(path, optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
