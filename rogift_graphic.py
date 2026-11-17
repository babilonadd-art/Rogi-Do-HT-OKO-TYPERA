"""Render dynamic picks on the user's exact original FT-corners poster."""
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TEMPLATE = Path(__file__).with_name("Screenshot_20261117-110724.png")
INK = (8, 20, 42)
BLUE = (0, 105, 205)
RED = (190, 20, 20)
NAVY_BGR = (53, 24, 5)


def _font(size, italic=False):
    paths = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"]
        if italic else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    )
    paths += ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in paths:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit(draw, text, max_width, size, italic=False):
    font = _font(size, italic)
    while size > 18 and draw.textbbox((0, 0), text, font=font)[2] > max_width:
        size -= 2
        font = _font(size, italic)
    return font


def _erase_text(image, box, mode):
    """Inpaint only the old variable lettering, retaining the poster texture."""
    x1, y1, x2, y2 = box
    region = image[y1:y2, x1:x2]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    if mode == "light":
        mask = (gray < 155).astype(np.uint8) * 255
    elif mode == "white":
        mask = (gray > 190).astype(np.uint8) * 255
    else:
        mask = (gray < 80).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    image[y1:y2, x1:x2] = cv2.inpaint(region, mask, 4, cv2.INPAINT_TELEA)


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12,
                        watermark_id=None):
    """Use the original poster unchanged except for its variable betting data."""
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"Brak oryginalnego wzoru grafiki: {TEMPLATE}")
    if len(legs) < 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch zdarzeń")

    source = cv2.imread(str(TEMPLATE), cv2.IMREAD_COLOR)
    if source is None:
        raise ValueError("Nie można odczytać oryginalnego wzoru grafiki")
    height, width = source.shape[:2]
    sx, sy = width / 1024.0, height / 1536.0
    def scaled_box(box):
        return tuple(round(value * (sx if index % 2 == 0 else sy))
                     for index, value in enumerate(box))
    def scaled_font(size, italic=False):
        return max(12, round(size * sy))
    image = source.copy()

    # Scale the reference layout to the exact dimensions of the repository image.
    for row_y in (400, 558):
        _erase_text(image, scaled_box((225, row_y, 765, row_y + 115)), "light")
        _erase_text(image, scaled_box((795, row_y + 40, 985, row_y + 115)), "light")
    for value_y in (700, 778, 845, 915):
        _erase_text(image, scaled_box((735, value_y, 984, value_y + 63)), "light")
    _erase_text(image, scaled_box((205, 1075, 550, 1160)), "white")
    _erase_text(image, scaled_box((585, 1075, 940, 1160)), "dark")

    poster = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(poster)

    # Two fixture panels: same positions and colors as the supplied artwork.
    for index, leg in enumerate(legs[:2]):
        y = round((400 + index * 158) * sy)
        home = str(leg["home"]).strip()
        away = str(leg["away"]).strip()
        match = f"{home} – {away}"
        draw.text((round(238 * sx), y), match,
                  font=_fit(draw, match, round(515 * sx), scaled_font(45)), fill=INK)

        line = float(leg["line"])
        draw.text((round(238 * sx), y + round(58 * sy)), "Over",
                  font=_font(scaled_font(40, italic=True), italic=True), fill=INK)
        draw.text((round(355 * sx), y + round(58 * sy)), f"{line:.1f}",
                  font=_font(scaled_font(42)), fill=BLUE)
        draw.text((round(440 * sx), y + round(58 * sy)), "rożnych FT",
                  font=_fit(draw, "rożnych FT", round(310 * sx),
                            scaled_font(40), italic=True), fill=INK)

        odds = f"{float(leg['odds']):.2f}"
        draw.text((round(802 * sx), y + round(48 * sy)), odds,
                  font=_fit(draw, odds, round(180 * sx), scaled_font(67)), fill=BLUE)

    # Financial figures follow the arithmetic and labels in the original poster.
    tax = round(float(stake) * float(tax_rate), 2)
    playing = round(float(stake) - tax, 2)
    payout = round(playing * float(total_odds), 2)
    profit = round(payout - float(stake), 2)

    values = [
        (f"{float(stake):,.2f} zł".replace(",", "X").replace(".", ",").replace("X", " "), INK, 708),
        (f"{tax:,.2f} zł".replace(",", "X").replace(".", ",").replace("X", " "), RED, 786),
        (f"{playing:,.2f} zł".replace(",", "X").replace(".", ",").replace("X", " "), INK, 863),
        (f"{float(total_odds):.2f}", BLUE, 940),
    ]
    for value, color, y in values:
        font = _fit(draw, value, 245, 48)
        width = draw.textbbox((0, 0), value, font=font)[2]
        draw.text((970 - width, y), value, font=font, fill=color)

    payout_text = f"{payout:,.2f} zł".replace(",", "X").replace(".", ",").replace("X", " ")
    profit_text = f"{profit:+,.2f} zł".replace(",", "X").replace(".", ",").replace("X", " ")
    draw.text((220, 1083), payout_text,
              font=_fit(draw, payout_text, 300, 55, italic=True), fill=(255, 255, 255))
    draw.text((600, 1083), profit_text,
              font=_fit(draw, profit_text, 330, 55), fill=(0, 0, 0))

    output = BytesIO()
    poster.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
