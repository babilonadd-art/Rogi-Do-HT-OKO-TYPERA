"""Replace only the two fixture labels on the untouched original poster."""
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TEMPLATE = Path(__file__).with_name("Screenshot_20261117-110724.png")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Coordinates are measured on the real 720x1640 source screenshot.
# Each rectangle covers only the existing fixture-name line, not the market,
# odds, cards, icons, background, totals, or any other part of the poster.
NAME_BOXES = (
    (168, 596, 548, 641),
    (168, 718, 548, 763),
)
NAME_ORIGINS = ((174, 600), (174, 722))
NAME_COLOR = (8, 18, 35)
MAX_NAME_WIDTH = 355


def _font_for(draw, text):
    for size in range(36, 12, -1):
        font = ImageFont.truetype(FONT_PATH, size)
        if draw.textbbox((0, 0), text, font=font)[2] <= MAX_NAME_WIDTH:
            return font
    return ImageFont.truetype(FONT_PATH, 12)


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12, watermark_id=None):
    """Return the original screenshot with only its two team-name labels replaced."""
    if len(legs) != 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch prawdziwych meczów")
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"Brak oryginalnego szablonu: {TEMPLATE}")

    original = Image.open(TEMPLATE).convert("RGB")
    if original.size != (720, 1640):
        raise ValueError(f"Nieoczekiwany rozmiar oryginalnego szablonu: {original.size}")

    # Remove only the old text areas with local inpainting. Everything outside
    # these two narrow rectangles remains byte-for-byte visually untouched.
    rgb = np.asarray(original)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
    for x1, y1, x2, y2 in NAME_BOXES:
        mask[y1:y2, x1:x2] = 255
    repaired = cv2.inpaint(bgr, mask, 9, cv2.INPAINT_TELEA)
    image = Image.fromarray(cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)

    for leg, (x, y) in zip(legs, NAME_ORIGINS):
        home = str(leg["home"]).strip()
        away = str(leg["away"]).strip()
        if not home or not away:
            raise ValueError("Brak prawdziwej nazwy jednej z drużyn")
        if home.lower() in {"mecz a", "mecz b", "mecz c", "mecz d", "drużyna a", "drużyna b"}:
            raise ValueError(f"Nieprawidłowa nazwa gospodarza: {home}")
        if away.lower() in {"mecz a", "mecz b", "mecz c", "mecz d", "drużyna a", "drużyna b"}:
            raise ValueError(f"Nieprawidłowa nazwa gościa: {away}")
        match = f"{home} – {away}"
        draw.text((x, y), match, font=_font_for(draw, match), fill=NAME_COLOR)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
