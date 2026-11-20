"""Render FT doubles by replacing text inside the user's original poster fields."""
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TZ = ZoneInfo("Europe/Warsaw")
# Native dimensions of the supplied original poster; never resize or crop it.
W, H = 1254, 1254
TEMPLATE = Path(__file__).resolve().parent / "Neonowy plakat typsterski Rogi do HT.png"
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


def original_artwork():
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"Brak oryginalnej grafiki użytkownika: {TEMPLATE}")
    with Image.open(TEMPLATE) as source:
        return source.convert("RGB").copy()


def _erase_existing_field_text(image):
    """Inpaint only printed letters/numbers; preserve the poster, borders and field colors."""
    rgb = np.asarray(image).copy()
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mask = np.zeros(gray.shape, dtype=np.uint8)

    def region(x1, y1, x2, y2, condition):
        roi = mask[y1:y2, x1:x2]
        roi[condition[y1:y2, x1:x2]] = 255

    # White team and market lettering inside the two original coupon rows.
    white_text = (gray > 165) & (hsv[:, :, 1] < 115)
    for x1, y1, x2, y2 in (
        (42, 316, 350, 380), (370, 316, 548, 380),
        (42, 405, 350, 469), (370, 405, 548, 469),
    ):
        region(x1, y1, x2, y2, white_text)

    # Dark odds digits on the original neon-green odds panels.
    dark_text = gray < 72
    for x1, y1, x2, y2 in ((566, 316, 700, 380), (566, 405, 700, 469)):
        region(x1, y1, x2, y2, dark_text)

    # Remove the template's preprinted footer values as well. These tight
    # rectangles contain VALUE text only; the printed labels stay intact.
    bright_footer_text = gray > 155
    for x1, y1, x2, y2 in (
        (92, 531, 222, 574), (302, 531, 440, 574),
        (500, 531, 702, 574),
    ):
        region(x1, y1, x2, y2, bright_footer_text)

    # A small dilation catches anti-aliased edges without reaching the neon frame.
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    repaired = cv2.inpaint(rgb, mask, 4, cv2.INPAINT_TELEA)
    return Image.fromarray(repaired)


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
        if not settled and not 8.5 <= line <= 9.5:
            raise ValueError("Dozwolone wyłącznie OVER 8.5–9.5 rożnych FT")
        if not settled and not 1.60 <= odds <= 2.20:
            raise ValueError("Kurs poza zakresem 1.60–2.20")
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

    # Existing team-name fields (two lines) and market / odds fields.
    for index, (home, away, league, line, odds, kickoff) in enumerate(rows):
        y = 316 + index * 89
        first_line = home
        second_line = f"– {away}"
        center(draw, (44, y, 350, y + 31), first_line,
               fit(draw, first_line, 292, 28, 15), WHITE)
        center(draw, (44, y + 29, 350, y + 61), second_line,
               fit(draw, second_line, 292, 28, 15), WHITE)
        center(draw, (370, y + 2, 548, y + 31), f"Over {line:.1f}",
               fit(draw, f"Over {line:.1f}", 168, 24, 16), WHITE)
        center(draw, (370, y + 30, 548, y + 60), "Rogów FT",
               fit(draw, "Rogów FT", 168, 22, 15), WHITE)
        center(draw, (566, y + 3, 700, y + 61), f"{odds:.2f}",
               fit(draw, f"{odds:.2f}", 126, 48, 28), BLACK)

    # Replace only the three numeric value fields in the original footer.
    # The labels ("Stawka", "Łączny kurs", "Potencjalna wygrana") and all
    # surrounding artwork remain untouched.
    # Write values directly into the original value fields; keep printed labels/artwork untouched.
    stake_text = f"{stake:.2f} PLN"
    combined_text = f"{combined_value:.2f}"
    payout_text = f"{stake * combined_value:.2f} PLN"
    footer_style = {"stroke_width": 2, "stroke_fill": (0, 0, 0)}
    center(draw, (92, 531, 222, 574), stake_text,
           fit(draw, stake_text, 126, 39, 23), GREEN, **footer_style)
    center(draw, (302, 531, 440, 574), combined_text,
           fit(draw, combined_text, 134, 48, 28), GREEN, **footer_style)
    center(draw, (500, 531, 702, 574), payout_text,
           fit(draw, payout_text, 198, 39, 21), (255, 235, 0), **footer_style)

    output = BytesIO()
    im.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
