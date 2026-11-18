"""Replace fixture names and FT corner lines on the original screenshot."""
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TEMPLATE = Path(__file__).with_name("Screenshot_20261117-110724.png")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Coordinates must be recalculated for the clean 1024x1536 poster before enabling publication.
# Each rectangle covers only the existing fixture-name line, not the market,
# odds, cards, icons, background, totals, or any other part of the poster.
NAME_BOXES = (
    (168, 596, 535, 641),
    (168, 718, 535, 763),
)
NAME_ORIGINS = ((174, 600), (174, 722))
NAME_COLOR = (8, 18, 35)
MAX_NAME_WIDTH = 350
HEADER_BOX = (250, 495, 620, 560)
HEADER_ORIGIN = (262, 503)

# Dynamic fields on the 720x1640 Telegram screenshot. The template itself
# contains sample values (including the obsolete 5.5 line), so all betting
# values must be erased and redrawn from the actual coupon data.
MARKET_BOXES = ((164, 640, 505, 686), (164, 752, 505, 798))
ODDS_BOXES = ((558, 616, 690, 690), (558, 728, 690, 802))
SUMMARY_VALUE_BOXES = (
    (510, 829, 685, 881), (510, 883, 685, 934),
    (510, 936, 685, 987), (510, 990, 685, 1041),
)
PAYOUT_BOX = (205, 1080, 395, 1145)
NET_PROFIT_BOX = (425, 1080, 685, 1145)
BLUE = (0, 105, 190)
NAVY = (8, 18, 35)
RED = (205, 24, 20)
WHITE = (255, 255, 255)
GREEN_TEXT = (0, 0, 0)

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


def _font(size, oblique=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf" if oblique else FONT_PATH
    return ImageFont.truetype(path, size)


def _right_text(draw, box, value, font, fill, right_padding=10):
    x1, y1, x2, y2 = box
    bounds = draw.textbbox((0, 0), value, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text((x2 - right_padding - width, y1 + (y2 - y1 - height) // 2 - bounds[1]),
              value, font=font, fill=fill)


def _draw_market_line(draw, box, line):
    x1, y1, x2, y2 = box
    y = y1 + 2
    font = _font(29, oblique=True)
    parts = (("Over ", NAVY), (f"{float(line):.1f}", BLUE), (" rożnych FT", NAVY))
    x = x1 + 2
    for label, color in parts:
        draw.text((x, y), label, font=font, fill=color)
        x += draw.textbbox((0, 0), label, font=font)[2]


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12, watermark_id=None, settled=False):
    """Use the repository screenshot and replace only the two fixture-name labels."""
    if len(legs) != 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch prawdziwych meczów")
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"Brak oryginalnego szablonu: {TEMPLATE}")
    for leg in legs:
        if not str(leg.get("home") or "").strip() or not str(leg.get("away") or "").strip():
            raise ValueError("Brak prawdziwej nazwy jednej z drużyn")
        if float(leg.get("line", 0)) < 9.5:
            raise ValueError("Blokada publikacji: rożne FT poniżej OVER 9.5")
        if not 1.70 <= float(leg.get("odds", 0)) <= 3.50:
            raise ValueError("Blokada publikacji: kurs pojedynczego typu poza zakresem 1.70–3.50")

    image = Image.open(TEMPLATE).convert("RGB")
    if image.size != (720, 1640):
        raise ValueError(f"Nieoczekiwany rozmiar oryginalnego szablonu: {image.size}")

    # Mask only fixture-name and market-line fields; preserve the rest of the screenshot.
    rgb = np.asarray(image)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
    for x1, y1, x2, y2 in (NAME_BOXES + MARKET_BOXES + (HEADER_BOX,) + SUMMARY_VALUE_BOXES):
        mask[y1:y2, x1:x2] = 255
    repaired = cv2.inpaint(bgr, mask, 9, cv2.INPAINT_TELEA)
    image = Image.fromarray(cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB))
    image = _reorder_summary_rows(image)
    draw = ImageDraw.Draw(image)

    # Prematch: only "ROGI FT" remains. After both legs are settled,
    # add the settlement status in the second header line.
    if settled:
        label = "DUBEL ROZLICZONY"
        font = _font(35, oblique=True)
        bounds = draw.textbbox((0, 0), label, font=font)
        x = HEADER_BOX[0] + (HEADER_BOX[2] - HEADER_BOX[0] - (bounds[2] - bounds[0])) // 2
        draw.text((x, HEADER_ORIGIN[1]), label, font=font, fill=NAVY)

    tax = round(float(stake) * float(tax_rate), 2)
    played_stake = round(float(stake) - tax, 2)
    summary_values = (
        (SUMMARY_VALUE_BOXES[0], f"{float(total_odds):.2f}", BLUE),
        (SUMMARY_VALUE_BOXES[1], f"{float(stake):.2f} zł", NAVY),
        (SUMMARY_VALUE_BOXES[2], f"{tax:.2f} zł", RED),
        (SUMMARY_VALUE_BOXES[3], f"{played_stake:.2f} zł", NAVY),
    )
    for box, value, color in summary_values:
        _right_text(draw, box, value, _font(37), color)

    for leg, (x, y), market_box in zip(legs, NAME_ORIGINS, MARKET_BOXES):
        match = f"{str(leg['home']).strip()} – {str(leg['away']).strip()}"
        draw.text((x, y), match, font=_font_for(draw, match), fill=NAME_COLOR)
        _draw_market_line(draw, market_box, leg["line"])

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
