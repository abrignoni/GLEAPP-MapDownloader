"""Render every icon from packaging/logo.svg, the one vector master.

    python tools/make_icons.py            rewrite the assets below
    python tools/make_icons.py --check    exit 1 if any asset differs from a fresh render

Writes:
    packaging/icon.icns            the macOS app icon, 16 to 1024 px (iconutil)
    packaging/icon.ico             the Windows icon, 16 to 256 px
    packaging/dmg_background.png   the disk image window, 960x540, drawn to match
    mapdownloader.py               the PNGs it carries inline: ICON_64 and ICON_32 for
                                   the window and taskbar, LOGO_96 for the window header

Requires rsvg-convert (librsvg), Pillow, and on macOS iconutil for the .icns. The
background's text is set in Arial Bold, or Helvetica when Arial is missing.
"""

from __future__ import annotations

import argparse
import base64
import io
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "packaging" / "logo.svg"
SCRIPT = ROOT / "mapdownloader.py"
ICNS = ROOT / "packaging" / "icon.icns"
ICO = ROOT / "packaging" / "icon.ico"
BACKGROUND = ROOT / "packaging" / "dmg_background.png"
EMBEDDED = {"ICON_64": 64, "ICON_32": 32, "LOGO_96": 96}
# GLEAPP's disk image colours, sampled from its packaging/dmg_background.png.
ORANGE, ARROW, WHITE, MUTED = (0xEF, 0xA0, 0x54), (0x99, 0x57, 0x23), (255, 255, 255), (0x8B, 0x93, 0xA3)
FONTS = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
         "/System/Library/Fonts/Helvetica.ttc"]


def render(size: int) -> Image.Image:
    png = subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(SVG)],
                         capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGBA")


def png_bytes(im: Image.Image) -> bytes:
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    return out.getvalue()


def make_icns(path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            render(base).save(iconset / f"icon_{base}x{base}.png")
            render(base * 2).save(iconset / f"icon_{base}x{base}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)


def make_ico(path: Path) -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    render(256).save(path, format="ICO", sizes=[(s, s) for s in sizes])


def font(size: int) -> ImageFont.FreeTypeFont:
    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    sys.exit("make_icons: no Arial Bold or Helvetica for the background text")


def make_background(path: Path) -> None:
    """The disk image window, laid out like GLEAPP's: an orange band fading into a white
    body, so Finder's own black icon labels stay readable. The app sits at (260, 290) and
    Applications at (610, 290) in packaging/dmg_settings.py, with the arrow between."""
    scale = 2                                     # drawn at 2x, then reduced, for smooth edges
    w, h = 960 * scale, 540 * scale
    im = Image.new("RGB", (w, h), WHITE)
    d = ImageDraw.Draw(im)
    band, fade = 136 * scale, 20 * scale
    d.rectangle([0, 0, w, band], fill=ORANGE)
    for i in range(fade):                         # the band fades into the body
        t = (i + 1) / (fade + 1)
        d.line([(0, band + i), (w, band + i)],
               fill=tuple(round(o + (255 - o) * t) for o in ORANGE))
    logo = render(88 * scale)
    im.paste(logo, (34 * scale, 22 * scale), logo)
    d.text((136 * scale, 28 * scale), "GLEAPP Map Downloader", font=font(34 * scale), fill=WHITE)
    d.text((136 * scale, 74 * scale), "Offline basemaps for GLEAPP", font=font(20 * scale), fill=WHITE)
    d.text((136 * scale, 104 * scale), "https://github.com/abrignoni/GLEAPP-MapDownloader",
           font=font(13 * scale), fill=WHITE)
    y = 290 * scale
    d.line([(385 * scale, y), (462 * scale, y)], fill=ARROW, width=14 * scale)
    d.polygon([(488 * scale, y), (440 * scale, y - 25 * scale), (440 * scale, y + 25 * scale)], fill=ARROW)
    text = "Drag GLEAPP Map Downloader to your Applications folder"
    f = font(18 * scale)
    tw = d.textlength(text, font=f)
    d.text(((w - tw) / 2, 452 * scale), text, font=f, fill=MUTED)
    im.resize((960, 540), Image.LANCZOS).save(path, optimize=True)


def embedded_source(text: str) -> str:
    """mapdownloader.py with its inline PNGs replaced by fresh renders."""
    for name, size in EMBEDDED.items():
        data = base64.b64encode(png_bytes(render(size))).decode()
        lines = "\n".join(f'    "{data[i:i + 92]}"' for i in range(0, len(data), 92))
        block = f"{name} = (\n{lines}\n)"
        pattern = re.compile(rf"^{name} = \(\n(?:    \"[^\"]*\"\n)+\)", re.M)
        if pattern.search(text):
            text = pattern.sub(lambda _m, b=block: b, text, count=1)
        else:
            anchor = "\n\n\nclass Cancelled(Exception):"
            assert text.count(anchor) == 1, "cannot find where to insert " + name
            text = text.replace(anchor, f"\n{block}{anchor}", 1)
    return text


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare with a fresh render, write nothing")
    args = ap.parse_args(argv)
    if not shutil.which("rsvg-convert"):
        sys.exit("make_icons: rsvg-convert (librsvg) is required")
    source = SCRIPT.read_text(encoding="utf-8")
    new_source = embedded_source(source)
    if args.check:
        stale = [] if new_source == source else [SCRIPT.name]
        with tempfile.TemporaryDirectory() as tmp:
            for path, maker in ((ICO, make_ico), (BACKGROUND, make_background)):
                fresh = Path(tmp) / path.name
                maker(fresh)
                if not path.exists() or fresh.read_bytes() != path.read_bytes():
                    stale.append(path.name)
        print("stale:", ", ".join(stale) if stale else "nothing (the .icns is not compared)")
        return 1 if stale else 0
    SCRIPT.write_text(new_source, encoding="utf-8")
    make_ico(ICO)
    make_background(BACKGROUND)
    if sys.platform == "darwin":
        make_icns(ICNS)
    else:
        print("make_icons: not on macOS, so packaging/icon.icns was left as it is")
    for p in (SCRIPT, ICO, BACKGROUND, ICNS):
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
