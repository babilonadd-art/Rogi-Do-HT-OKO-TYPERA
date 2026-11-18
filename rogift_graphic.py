"""Clean, fully rendered FT-corners double poster (no screenshot patching)."""
from io import BytesIO
from pathlib import Path
import math

from PIL import Image, ImageDraw, ImageFont

W, H = 720, 1280
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ITALIC_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"
WHITE = (245, 249, 255)
MUTED = (158, 181, 207)
NAVY = (5, 13, 31)
BLUE = (0, 139, 255)
LIME = (145, 255, 0)
YELLOW = (255, 211, 0)
RED = (255, 68, 68)


def _font(size, italic=False):
    return ImageFont.truetype(ITALIC_PATH if italic else FONT_PATH, size)


def _fit(draw, text, max_width, start=34, minimum=17, italic=False):
    for size in range(start, minimum - 1, -1):
        f = _font(size, italic)
        if draw.textbbox((0, 0), text, font=f)[2] <= max_width:
            return f
    return _font(minimum, italic)


def _center(draw, xy, text, font, fill):
    x1, y1, x2, y2 = xy
    b = draw.textbbox((0, 0), text, font=font)
    x = x1 + (x2 - x1 - (b[2] - b[0])) / 2
    y = y1 + (y2 - y1 - (b[3] - b[1])) / 2 - b[1]
    draw.text((x, y), text, font=font, fill=fill)


def _right(draw, xy, text, font, fill, pad=18):
    x1, y1, x2, y2 = xy
    b = draw.textbbox((0, 0), text, font=font)
    y = y1 + (y2 - y1 - (b[3] - b[1])) / 2 - b[1]
    draw.text((x2 - pad - (b[2] - b[0]), y), text, font=font, fill=fill)


def _rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _make_background():
    image = Image.new("RGB", (W, H))
    px = image.load()
    for y in range(H):
        t = y / H
        base = (int(4 + 5*t), int(12 + 9*t), int(31 + 20*t))
        for x in range(W):
            glow = max(0, 1 - abs(x - W/2)/(W/2)) * 7
            px[x, y] = (min(255, base[0] + int(glow/3)),
                        min(255, base[1] + int(glow/2)),
                        min(255, base[2] + int(glow)))
    d = ImageDraw.Draw(image, "RGBA")
    # Stadium floodlights and restrained beams.
    for cx in (42, W-42):
        for i in range(6):
            x = cx + (i-2.5)*13
            d.ellipse((x-5, 54, x+5, 64), fill=(205, 235, 255, 245))
        d.ellipse((cx-80, 20, cx+80, 120), fill=(30, 120, 255, 16))
    # Fine pitch lines at the bottom.
    d.line((0, 1190, W, 1190), fill=(60, 190, 90, 75), width=2)
    for x in range(-150, W+150, 90):
        d.line((x, 1280, W/2 + (x-W/2)*0.25, 1190), fill=(70, 180, 100, 35), width=2)
    return image


def _draw_match(draw, top, leg, index):
    box = (28, top, 692, top + 174)
    _rounded(draw, box, 24, (7, 20, 43), (0, 137, 255), 3)
    _rounded(draw, (38, top+10, 682, top+164), 18, (8, 24, 48), (130, 255, 0), 1)
    draw.text((58, top+22), f"MECZ {index}", font=_font(17), fill=LIME)
    home = str(leg["home"]).strip()
    away = str(leg["away"]).strip()
    draw.text((58, top+52), home, font=_fit(draw, home, 480, 32, 18), fill=WHITE)
    draw.text((58, top+91), away, font=_fit(draw, away, 480, 32, 18), fill=WHITE)
    line = f"OVER {float(leg['line']):.1f} ROŻNYCH FT"
    _rounded(draw, (56, top+130, 410, top+158), 12, (145, 255, 0))
    _center(draw, (56, top+130, 410, top+158), line, _font(17), NAVY)
    draw.text((520, top+23), "KURS", font=_font(17), fill=MUTED)
    _center(draw, (500, top+52, 670, top+130), f"{float(leg['odds']):.2f}", _font(48, True), BLUE)


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12, watermark_id=None, settled=False):
    """Render the entire poster from live coupon values on a clean canvas."""
    if len(legs) != 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch prawdziwych meczów")
    for leg in legs:
        if not str(leg.get("home") or "").strip() or not str(leg.get("away") or "").strip():
            raise ValueError("Brak prawdziwej nazwy jednej z drużyn")
        if float(leg.get("line", 0)) < 9.5:
            raise ValueError("Blokada publikacji: rożne FT poniżej OVER 9.5")
        if not 1.70 <= float(leg.get("odds", 0)) <= 3.50:
            raise ValueError("Blokada publikacji: kurs pojedynczego typu poza zakresem 1.70–3.50")

    image = _make_background()
    draw = ImageDraw.Draw(image, "RGBA")

    # Brand and main header.
    _center(draw, (35, 24, 685, 78), "OKO TYPERA", _font(31, True), WHITE)
    _rounded(draw, (34, 92, 686, 220), 25, (10, 28, 56), (145, 255, 0), 4)
    title = "ROGI FT — DUBEL ROZLICZONY" if settled else "ROGI FT"
    title_font = _fit(draw, title, 610, 52 if settled else 64, 30, True)
    _center(draw, (48, 105, 672, 205), title, title_font, LIME)

    _draw_match(draw, 250, legs[0], 1)
    _draw_match(draw, 442, legs[1], 2)

    # Four clearly ordered bookmaker-style summary boxes.
    tax = round(float(stake) * float(tax_rate), 2)
    played = round(float(stake) - tax, 2)
    summaries = [
        ("KURS ŁĄCZNY", f"{float(total_odds):.2f}", BLUE),
        ("STAWKA", f"{float(stake):.2f} zł", WHITE),
        ("PODATEK 12%", f"{tax:.2f} zł", RED),
        ("KWOTA GRAJĄCA", f"{played:.2f} zł", YELLOW),
    ]
    left, gap, box_w = 24, 10, 160
    for i, (label, value, color) in enumerate(summaries):
        x = left + i*(box_w+gap)
        box = (x, 650, x+box_w, 825)
        _rounded(draw, box, 20, (4, 17, 38), (0, 145, 255), 3)
        _rounded(draw, (x+5, 655, x+box_w-5, 820), 16, (5, 22, 46), (145, 255, 0), 1)
        _center(draw, (x+7, 672, x+box_w-7, 728), label, _fit(draw, label, box_w-20, 18, 12), WHITE)
        _center(draw, (x+5, 735, x+box_w-5, 800), value, _fit(draw, value, box_w-16, 30, 17, True), color)

    # Bottom panel stays neutral before kick-off and changes only after settlement.
    _rounded(draw, (28, 870, 692, 1118), 25, (6, 21, 44), (0, 137, 255), 3)
    if settled:
        _center(draw, (48, 890, 672, 965), "DUBEL ROZLICZONY", _font(34, True), LIME)
        _center(draw, (48, 980, 672, 1055), "WYNIK ZOSTAŁ ZWERYFIKOWANY", _font(21), WHITE)
    else:
        _center(draw, (48, 900, 672, 978), "DWA MECZE • JEDEN DUBEL", _font(29, True), LIME)
        _center(draw, (48, 990, 672, 1055), "POWODZENIA!", _font(28, True), WHITE)

    _center(draw, (35, 1150, 685, 1200), "OKO TYPERA  •  RZUTY ROŻNE FT", _font(19), MUTED)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
