"""Regenerate tests/data (needs Pillow; reference decoder = libjpeg via Pillow).
small_*.jpg + .rgb : conformance vectors (decoded by libjpeg) for stb_image.
canvas.jpg         : 640x656 canvas, each key (col,row) a flat colour, subscreen white.
wrong.jpg          : 320x200 (must be rejected with -2)."""
import os, random
from PIL import Image
D = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(D, exist_ok=True)
random.seed(1)
def small(name, w, h, **kw):
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 255) // w, (y * 255) // h, ((x ^ y) * 7) & 255)
    im.save(os.path.join(D, name + ".jpg"), "JPEG", **kw)
    ref = Image.open(os.path.join(D, name + ".jpg")).convert("RGB")
    open(os.path.join(D, name + ".rgb"), "wb").write(ref.tobytes())
small("small_420", 64, 48, quality=90, subsampling=2)
small("small_444", 64, 48, quality=95, subsampling=0)
small("small_prog", 61, 37, quality=85, subsampling=2, progressive=True)
small("small_422", 33, 17, quality=80, subsampling=1)
def key_colour(c, r):
    return ((c * 60) & 255, (r * 80) & 255, (255 - c * 40 - r * 20) & 255)
cv = Image.new("RGB", (640, 656), (0, 0, 0))
from PIL import ImageDraw
d = ImageDraw.Draw(cv)
d.rectangle([106, 0, 106 + 428 - 1, 141], fill=(255, 255, 255))
for r in range(4):
    for c in range(5):
        x, y = c * 128, 144 + r * 128
        d.rectangle([x, y, x + 127, y + 127], fill=key_colour(c, r))
cv.save(os.path.join(D, "canvas.jpg"), "JPEG", quality=90)
Image.new("RGB", (320, 200), (10, 20, 30)).save(os.path.join(D, "wrong.jpg"), "JPEG")
