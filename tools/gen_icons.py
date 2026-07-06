"""Generate ELN desktop icons (.ico): a clean flask on a rounded gradient tile.
Local = warm clay, Cloud = cool blue with a small cloud badge."""
import sys
from PIL import Image, ImageDraw

S = 1024  # supersample; downscaled to 256 for crisp edges


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_tile(top, bottom, radius):
    grad = Image.new("RGB", (S, S), top)
    d = ImageDraw.Draw(grad)
    for y in range(S):
        d.line([(0, y), (S, y)], fill=lerp(top, bottom, y / S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([28, 28, S - 28, S - 28], radius=radius, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def draw_flask(img, white=(251, 248, 242), liquid=(126, 176, 138)):
    d = ImageDraw.Draw(img)
    # flask body + neck silhouette
    flask = [(452, 250), (452, 470), (322, 812), (702, 812), (572, 470), (572, 250)]
    d.polygon(flask, fill=white)
    # top rim
    d.rounded_rectangle([398, 214, 626, 258], radius=22, fill=white)
    # liquid band inside lower body
    liq = [(404, 606), (620, 606), (702, 812), (322, 812)]
    d.polygon(liq, fill=liquid)
    # bubbles
    for (cx, cy, r) in [(470, 560, 20), (540, 520, 15), (505, 585, 12)]:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=liquid)


def draw_cloud(img, color=(251, 248, 242)):
    d = ImageDraw.Draw(img)
    # small cloud badge, top-right
    d.ellipse([610, 250, 720, 360], fill=color)
    d.ellipse([680, 230, 810, 360], fill=color)
    d.ellipse([740, 270, 840, 370], fill=color)
    d.rounded_rectangle([630, 320, 830, 372], radius=26, fill=color)


def save_ico(img, path):
    small = img.resize((256, 256), Image.LANCZOS)
    small.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", path)


root = sys.argv[1].rstrip("/\\")

# local: warm clay tile + flask
local = rounded_tile((196, 118, 66), (168, 90, 48), radius=200)
draw_flask(local)
save_ico(local, root + "/eln_local.ico")

# cloud: cool blue tile + flask + cloud badge
cloud = rounded_tile((84, 148, 196), (46, 100, 150), radius=200)
draw_flask(cloud, liquid=(210, 232, 246))
draw_cloud(cloud)
save_ico(cloud, root + "/eln_cloud.ico")
