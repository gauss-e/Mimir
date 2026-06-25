"""Regenerate banner_art.MIMIR_ART from the reference portrait.

Downscales the pixel-art reference (God of War Mimir) to a small grid and
renders it as a truecolor half-block (▀) portrait: each terminal cell stacks
two vertical pixels — fg = top pixel, bg = bottom pixel — so a square source
image stays square on screen (cells are ~2× tall).

    python _gen_banner.py <image> [width]

Writes src/banner_art.py.  Needs pillow:  uv run --with pillow python _gen_banner.py …
"""

from __future__ import annotations

import sys

from PIL import Image

WIDTH = 32  # output columns (also pixel-rows = WIDTH for a square source)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def emit(path: str, width: int = WIDTH) -> str:
    im = Image.open(path).convert("RGB")
    h = round(width * im.size[1] / im.size[0])
    if h % 2:
        h += 1
    im = im.resize((width, h), Image.BOX)
    px = im.load()
    rows = []
    for y in range(0, h, 2):
        cells = [f"[{_hex(px[x, y])} on {_hex(px[x, y + 1])}]▀[/]"
                 for x in range(width)]
        rows.append("    '" + "".join(cells) + "',")
    return "\n".join(rows)


HEADER = '''"""Pixel-art Mimir (God of War) for the welcome banner.

A downscale of the reference portrait (ram horns, the glowing gold eye,
grizzled beard) rendered as a truecolor half-block (▀) grid: each cell's
fg = top pixel, bg = bottom pixel. Regenerate with _gen_banner.py — do not
hand-edit.
"""

MIMIR_ART = [
'''


if __name__ == "__main__":
    path = sys.argv[1]
    width = int(sys.argv[2]) if len(sys.argv) > 2 else WIDTH
    art = emit(path, width)
    with open("src/banner_art.py", "w") as f:
        f.write(HEADER)
        f.write(art)
        f.write("\n]\n")
    print(f"wrote src/banner_art.py ({width} cols)")
