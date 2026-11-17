"""Replace fixture labels and reorder the summary on the original poster."""
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
    (168, 596, 535, 641),
    (168, 718, 535, 763),
)
NAME_ORIGINS = ((174, 600), (174, 722))
NAME_COLOR = (8, 18, 35)
MAX_NAME_WIDTH = 350

# Four summary-row content strips, from top to bottom in the source image:
# Stawka, Podatek 12%, Kwota grająca, Kurs łączny.
# Reassemble them as: Kurs łączny, Stawka, Podatek 12%, Kwota grająca.
# Separator rules remain in their original positions.
SUMMARY_ROW_BANDS = ((829, 881), (883, 934), (936, 987), (990, 1041))
SUMMARY_ROW_ORDER = (3, 0, 1, 2)
SUMMARY_ROW_LEFT = 35
SUMMARY_ROW_RIGHT = 690


def _reorder_summary_rows(image):
    rows = [
        image.crop((SUMMARY_ROW_LEFT, top, SUMMARY_ROW_RIGHT, bottom))
        for top, bottom in SUMMARY_ROW_BANDS
    ]
    for slot, source_index in enumerate(SUMMARY_ROW_ORDER):
        top, bottom = SUMMARY_ROW_BANDS[slot]
        row = rows[source_index]
        target_height = bottom - top
        if row.height != target_height:
            row = row.resize((row.width, target_height), Image.Resampling.LANCZOS)
        image.paste(row, (SUMMARY_ROW_LEFT, top))
    return image


def _font_for(draw, text):
    for size in range(36, 12, -1):
        font = ImageFont.truetype(FONT_PATH, size)
        if draw.textbbox((0, 0), text, font=font)[2] <= MAX_NAME_WIDTH:
            return font
    return ImageFont.truetype(FONT_PATH, 12)


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12, watermark_id=None):
    """Return the original screenshot with reordered totals and real team names."""
    if len(legs) != 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch prawdziwych meczów")
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"Brak oryginalnego szablonu: {TEMPLATE}")

    original = Image.open(TEMPLATE).convert("RGB")
    if original.size != (720, 1640):
        raise ValueError(f"Nieoczekiwany rozmiar oryginalnego szablonu: {original.size}")

    original = _reorder_summary_rows(original)

    # Remove only the old text areas with local inpainting. Everything outside
    # these two narrow rectangles remains unchanged apart from the requested
    # four-row summary reorder above.
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
        match = f"{home} – {away}"
        draw.text((x, y), match, font=_font_for(draw, match), fill=NAME_COLOR)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
