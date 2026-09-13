#!/usr/bin/env python3
"""홈 화면 아이콘 생성. 디자인 툴 없이 재현 가능하게 코드로 그린다.

    python scripts/make_icons.py
"""

import pathlib

from PIL import Image, ImageDraw

OUT = pathlib.Path(__file__).resolve().parent.parent / "nowbus" / "static" / "icons"
BG = (31, 111, 235)
INK = (255, 255, 255)
GLASS = (31, 111, 235)


def draw_bus(size: int, pad_ratio: float) -> Image.Image:
    """정면에서 본 버스. 작게 줄여도 형태가 남도록 요소를 크고 단순하게 둔다."""
    s = size * 4  # 4배로 그린 뒤 축소해서 계단현상을 없앤다
    img = Image.new("RGBA", (s, s), BG + (255,))
    d = ImageDraw.Draw(img)

    pad = int(s * pad_ratio)
    w = s - pad * 2
    body = (pad + w * 0.14, pad + w * 0.08, pad + w * 0.86, pad + w * 0.84)
    d.rounded_rectangle(body, radius=int(w * 0.14), fill=INK)

    # 앞유리
    gx0, gy0 = body[0] + w * 0.09, body[1] + w * 0.10
    gx1, gy1 = body[2] - w * 0.09, body[1] + w * 0.34
    d.rounded_rectangle((gx0, gy0, gx1, gy1), radius=int(w * 0.05), fill=GLASS)

    # 헤드라이트
    r = w * 0.045
    cy = body[3] - w * 0.13
    for cx in (body[0] + w * 0.13, body[2] - w * 0.13):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GLASS)

    # 바퀴
    wy0, wy1 = body[3] - w * 0.02, body[3] + w * 0.10
    ww = w * 0.14
    for wx in (body[0] + w * 0.06, body[2] - w * 0.06 - ww):
        d.rounded_rectangle((wx, wy0, wx + ww, wy1), radius=int(w * 0.03), fill=INK)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (180, 192, 512):
        draw_bus(size, 0.14).convert("RGB").save(OUT / f"icon-{size}.png")
    # maskable 은 원형으로 잘려도 잘리지 않도록 안전영역(80%) 안에 그린다
    draw_bus(512, 0.26).convert("RGB").save(OUT / "icon-512-maskable.png")
    for f in sorted(OUT.glob("*.png")):
        print(f"  {f.name}  {f.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
