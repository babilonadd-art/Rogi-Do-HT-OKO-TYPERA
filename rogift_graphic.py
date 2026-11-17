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

# Dynamic fields on the 720x1640 Telegram screenshot. The template itself
# contains sample values (including the obsolete 5.5 line), so all betting
# values must be erased and redrawn from the actual coupon data.
MARKET_BOXES = ((164, 640, 505, 686), (164, 752, 505, 798))
ODDS_BOXES = ((558, 616, 690, 690), (558, 728, 690, 802))
SUMMARY_VALUE_BOXES = (
    (510, 829, 685, 881), (510, 883, 685, 934),
    (510, 936, 685, 987), (510, 990, 685, 1041),
)
PAYOUT_BOX = (145, 1058, 548, 1148)
NET_PROFIT_BOX = (410, 1058, 690, 1148)
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


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12, watermark_id=None):
    """Render every selection and amount from the saved coupon, never template sample data."""
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

    original = Image.open(TEMPLATE).convert("RGB")
    if original.size != (720, 1640):
        raise ValueError(f"Nieoczekiwany rozmiar oryginalnego szablonu: {original.size}")
    original = _reorder_summary_rows(original)

    stake = float(stake)
    tax = round(stake * float(tax_rate), 2)
    playing_amount = round(stake - tax, 2)
    payout = round(playing_amount * float(total_odds), 2)
    net_profit = round(payout - stake, 2)

    # Erase all sample selections, prices and totals before writing live data.
    rgb = np.asarray(original)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
    boxes = (NAME_BOXES + MARKET_BOXES + ODDS_BOXES + SUMMARY_VALUE_BOXES
             + (PAYOUT_BOX, NET_PROFIT_BOX))
    for x1, y1, x2, y2 in boxes:
        mask[y1:y2, x1:x2] = 255
    repaired = cv2.inpaint(bgr, mask, 9, cv2.INPAINT_TELEA)
    image = Image.fromarray(cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)

    for leg, (x, y), market_box, odds_box in zip(legs, NAME_ORIGINS, MARKET_BOXES, ODDS_BOXES):
        home = str(leg["home"]).strip()
        away = str(leg["away"]).strip()
        match = f"{home} – {away}"
        draw.text((x, y), match, font=_font_for(draw, match), fill=NAME_COLOR)
        _draw_market_line(draw, market_box, leg["line"])
        _right_text(draw, odds_box, f"{float(leg['odds']):.2f}", _font(55), BLUE, 8)

    # After row reordering the order is: Kurs łączny, Stawka, Podatek, Kwota grająca.
    summary = (
        (f"{float(total_odds):.2f}", BLUE),
        (f"{stake:.2f}".replace(".", ",") + " zł", NAVY),
        (f"{tax:.2f}".replace(".", ",") + " zł", RED),
        (f"{playing_amount:.2f}".replace(".", ",") + " zł", NAVY),
    )
    for box, (value, color) in zip(SUMMARY_VALUE_BOXES, summary):
        _right_text(draw, box, value, _font(34), color, 8)

    _right_text(draw, PAYOUT_BOX, f"{payout:.2f}".replace(".", ",") + " zł",
                _font(48), WHITE, 8)
    _right_text(draw, NET_PROFIT_BOX,
                ("+" if net_profit >= 0 else "") + f"{net_profit:.2f}".replace(".", ",") + " zł",
                _font(42), GREEN_TEXT, 8)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
