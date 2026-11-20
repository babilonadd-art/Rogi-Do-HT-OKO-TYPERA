"""Render FT doubles by replacing text inside the user's original poster fields."""
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TZ = ZoneInfo("Europe/Warsaw")
# Native dimensions of the supplied original poster; never resize or crop it.
W, H = 720, 751
TEMPLATE = Path(__file__).resolve().parent / "rogi_ft_template_new.png"
WHITE = (255, 255, 255)
GREEN = (143, 255, 0)
RED = (255, 45, 55)
BLACK = (0, 0, 0)


def font(size):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def fit(draw, value, max_width, high=28, low=12):
    for size in range(high, low - 1, -1):
        candidate = font(size)
        if draw.textbbox((0, 0), value, font=candidate)[2] <= max_width:
            return candidate
    return font(low)


def center(draw, bounds, value, selected_font, color, **text_options):
    x1, y1, x2, y2 = bounds
    box = draw.textbbox((0, 0), value, font=selected_font)
    width, height = box[2] - box[0], box[3] - box[1]
    draw.text(
        (x1 + (x2 - x1 - width) / 2, y1 + (y2 - y1 - height) / 2 - box[1]),
        value, font=selected_font, fill=color, **text_options,
    )


def money(value):
    return f"{float(value):.2f}".replace(".", ",") + " PLN"


def calculate_coupon_totals(leg_odds, stake):
    """Round the displayed combined odds first, then calculate the displayed payout."""
    if len(leg_odds) != 2:
        raise ValueError("Dubel musi zawierać dokładnie dwa kursy")
    combined = (Decimal(str(leg_odds[0])) * Decimal(str(leg_odds[1]))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    payout = (Decimal(str(stake)) * combined).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return combined, payout


def original_artwork():
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"Brak oryginalnej grafiki użytkownika: {TEMPLATE}")
    with Image.open(TEMPLATE) as source:
        if source.size != (720, 751):
            raise ValueError(f"Nieprawidłowy rozmiar nowego szablonu: {source.size}; oczekiwano 720x751")
        return source.convert("RGB").copy()


def _erase_existing_field_text(image):
    """The latest supplied template already has clean, empty dynamic fields."""
    return image.copy()


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=.12,
                        watermark_id=None, settled=False, night=False):
    if len(legs) != 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch meczów")

    rows = []
    for leg in legs:
        home = str(leg.get("home") or "").strip()
        away = str(leg.get("away") or "").strip()
        league = str(leg.get("league") or "").strip()
        if (not home or not away or not league or
                home.lower() in {"a", "b", "mecz a", "mecz b", "team a", "team b"} or
                away.lower() in {"a", "b", "mecz a", "mecz b", "team a", "team b"}):
            raise ValueError("Brak prawdziwej nazwy drużyny lub ligi")
        line = float(leg.get("line", 0))
        odds = float(leg.get("odds", 0))
        # New picks are filtered in RogiFTDuble.py; the renderer also has to
        # reprint legitimate historical coupons whose old odds/lines differ.
        if not settled and not 8.5 <= line <= 9.5:
            raise ValueError("Nieprawidłowa linia rożnych FT")
        if not settled and odds <= 1.0:
            raise ValueError("Nieprawidłowy kurs meczu")
        if leg.get("start_ts") is None:
            raise ValueError("Brak godziny rozpoczęcia meczu")
        kickoff = datetime.fromtimestamp(
            int(leg["start_ts"]), timezone.utc
        ).astimezone(TZ).strftime("%H:%M")
        rows.append((home, away, league, line, odds, kickoff))

    stake = float(stake)
    total_odds = float(total_odds)
    tax_rate = float(tax_rate)
    if stake <= 0 or total_odds <= 0 or not 0 <= tax_rate < 1:
        raise ValueError("Nieprawidłowa stawka, kurs lub podatek")
    combined_value = rows[0][4] * rows[1][4]
    if abs(combined_value - total_odds) > .02:
        raise ValueError("Kurs łączny nie zgadza się z kursami obu meczów")

    # Keep the uploaded artwork at its exact native dimensions. No new cards,
    # dark rectangles, resizing, cropping or replacement background are added.
    # Use the original poster artwork directly. Do not erase or inpaint existing fields.
    # Clear all dynamic fields before writing fresh odds and calculated payout.
    im = _erase_existing_field_text(original_artwork())
    draw = ImageDraw.Draw(im)

    # Latest blank poster: two fixture rows, with kickoff shown under team names.
    for index, (home, away, league, line, odds, kickoff) in enumerate(rows):
        y = 300 + index * 88
        fixture_text = f"{home} – {away}"
        center(draw, (28, y + 4, 348, y + 38), fixture_text,
               fit(draw, fixture_text, 310, 24, 13), WHITE)
        kickoff_text = f"Start: {kickoff}"
        center(draw, (28, y + 39, 348, y + 66), kickoff_text,
               fit(draw, kickoff_text, 310, 19, 14), WHITE)
        market_top = f"Over {line:.1f}"
        center(draw, (368, y + 4, 525, y + 36), market_top,
               fit(draw, market_top, 150, 23, 15), WHITE)
        center(draw, (368, y + 37, 525, y + 66), "Rogów FT",
               fit(draw, "Rogów FT", 150, 20, 14), WHITE)
        center(draw, (540, y + 4, 691, y + 66), f"{odds:.2f}",
               fit(draw, f"{odds:.2f}", 142, 43, 25), BLACK)

    # Stake (50 PLN) is preprinted. Fill the combined odds and payout.
    displayed_total, displayed_payout = calculate_coupon_totals(
        [rows[0][4], rows[1][4]], stake
    )
    combined_text = f"{displayed_total:.2f}"
    payout_text = f"{displayed_payout:.2f} PLN"
    footer_style = {"stroke_width": 2, "stroke_fill": (0, 0, 0)}
    center(draw, (300, 510, 420, 550), combined_text,
           fit(draw, combined_text, 116, 37, 22), GREEN, **footer_style)
    center(draw, (520, 510, 698, 550), payout_text,
           fit(draw, payout_text, 170, 31, 17), (255, 235, 0), **footer_style)
    output = BytesIO()
    im.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
