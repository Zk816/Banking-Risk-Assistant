import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PUBLIC = Path(__file__).resolve().parents[1] / "public"
BLUE_TOP, BLUE_BOTTOM = (79, 140, 255), (29, 78, 216)
NAVY = (9, 14, 28)
SS = 4


def font(path: str, size: int, weight: str) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(path, size)
    f.set_variation_by_name(weight)
    return f


def gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    g = Image.new("RGBA", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        g.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    return g.resize((w, h))


def mark(size: int) -> Image.Image:
    s = size * SS
    tile = gradient(s, s, BLUE_TOP, BLUE_BOTTOM)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.27), fill=255)
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    d = ImageDraw.Draw(out)
    pts = [(0.20, 0.70), (0.40, 0.52), (0.55, 0.60), (0.78, 0.32)]
    pts = [(x * s, y * s) for x, y in pts]
    d.line(pts, fill=(255, 255, 255, 255), width=int(s * 0.085), joint="curve")
    for x, y in pts[:1]:
        r = s * 0.0425
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 255))
    x, y = pts[-1]
    r = s * 0.075
    d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 255))
    return out.resize((size, size), Image.LANCZOS)


def logo(path: Path, fpath: str, ink: tuple) -> None:
    h = 112
    f = font(fpath, 66 * SS, "SemiBold")
    text = "Fiscalis"
    tw = int(f.getlength(text)) + 8 * SS
    canvas = Image.new("RGBA", ((h + 24) * SS + tw, h * SS), (0, 0, 0, 0))
    canvas.alpha_composite(mark(h * SS), (0, 0))
    ImageDraw.Draw(canvas).text(((h + 24) * SS, h * SS // 2), text, font=f, fill=ink, anchor="lm")
    canvas.resize((canvas.width // SS, canvas.height // SS), Image.LANCZOS).save(path)


def login_panel(path: Path, fpath: str) -> None:
    W, H = 1200, 1600
    im = Image.new("RGBA", (W, H), NAVY + (255,))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((W * 0.25, H * 0.42, W * 1.25, H * 1.05), fill=(37, 99, 235, 150))
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(230)))
    d = ImageDraw.Draw(im)
    for y in range(80, H, 64):
        for x in range(80, W, 64):
            d.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5), fill=(255, 255, 255, 22))
    line = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
    ld = ImageDraw.Draw(line)
    pts = []
    for i in range(0, 61):
        t = i / 60
        pts.append(
            (t * W * 2, (0.60 - 0.36 * t + 0.03 * math.sin(t * 17) + 0.018 * math.sin(t * 41)) * H * 2)
        )
    ld.polygon(pts + [(W * 2, H * 2), (0, H * 2)], fill=(59, 130, 246, 26))
    ld.line(pts, fill=(147, 197, 253, 230), width=6, joint="curve")
    x, y = pts[-8]
    ld.ellipse((x - 14, y - 14, x + 14, y + 14), fill=(219, 234, 254, 255))
    im.alpha_composite(line.resize((W, H), Image.LANCZOS))
    im.alpha_composite(mark(96), (130, 300))
    d = ImageDraw.Draw(im)
    d.text((130, H - 470), "Fiscalis", font=font(fpath, 76, "SemiBold"), fill=(255, 255, 255, 255))
    for k, line in enumerate(["Kazakhstan banking data,", "with the source behind every number."]):
        d.text((133, H - 370 + k * 50), line, font=font(fpath, 36, "Regular"), fill=(170, 186, 214, 255))
    im.convert("RGB").save(path, optimize=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--font", required=True, help="path to Inter variable TTF")
    a = p.parse_args()
    (PUBLIC / "avatars").mkdir(parents=True, exist_ok=True)
    logo(PUBLIC / "logo_dark.png", a.font, (242, 244, 248, 255))
    logo(PUBLIC / "logo_light.png", a.font, (15, 18, 26, 255))
    mark(64).save(PUBLIC / "favicon.png")
    mark(256).save(PUBLIC / "avatars" / "fiscalis.png")
    login_panel(PUBLIC / "login.png", a.font)
    print("wrote", ", ".join(p.name for p in sorted(PUBLIC.glob("*.png"))))


if __name__ == "__main__":
    main()
