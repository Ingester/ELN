"""ELN desktop icon: clean clay flask on a light cream tile. Light/warm, no dark bg, no green."""
import sys
from PIL import Image, ImageDraw

S = 1024


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_tile(top, bottom, radius, border=None):
    grad = Image.new("RGB", (S, S), top)
    d = ImageDraw.Draw(grad)
    for y in range(S):
        d.line([(0, y), (S, y)], fill=lerp(top, bottom, y / S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([28, 28, S - 28, S - 28], radius=radius, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    if border:
        ImageDraw.Draw(out).rounded_rectangle([28, 28, S - 28, S - 28], radius=radius, outline=border, width=6)
    return out


def draw_flask(img, clay=(193, 107, 61), liquid=(236, 181, 130)):
    d = ImageDraw.Draw(img)
    flask = [(452, 250), (452, 470), (322, 812), (702, 812), (572, 470), (572, 250)]
    d.polygon(flask, fill=clay)
    d.rounded_rectangle([398, 214, 626, 258], radius=22, fill=clay)
    d.polygon([(404, 620), (620, 620), (702, 812), (322, 812)], fill=liquid)


def main(root):
    tile = rounded_tile((247, 244, 238), (237, 231, 221), radius=200, border=(228, 222, 210))
    draw_flask(tile)
    small = tile.resize((256, 256), Image.LANCZOS)
    small.save(root + "/eln.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    # preview strip at 128 + 32 on a neutral bg
    prev = Image.new("RGB", (220, 200), (235, 235, 235))
    prev.paste(small.resize((128, 128), Image.LANCZOS), (46, 12), small.resize((128, 128), Image.LANCZOS))
    s32 = small.resize((32, 32), Image.LANCZOS)
    prev.paste(s32, (94, 150), s32)
    prev.save(root + "/_icon_preview.png")
    print("wrote", root + "/eln.ico")


main(sys.argv[1].rstrip("/\\"))
