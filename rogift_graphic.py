"""Render FT-corners doubles on a freshly drawn Oko Typera poster.

No screenshot/template containing old text is loaded or reused. Every pixel,
including the background and all text panels, is generated for each coupon.
"""
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import math

W, H = 1024, 1536
NAVY = (5, 19, 43)
NAVY2 = (10, 43, 91)
BLUE = (0, 126, 232)
PALE = (244, 248, 253)
WHITE = (255, 255, 255)
GREEN = (143, 255, 0)
DARK = (5, 22, 47)
RED = (195, 36, 45)


def _font(size):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit(draw, text, max_width, start=40, minimum=16):
    for size in range(start, minimum - 1, -1):
        font = _font(size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    raise ValueError(f"Tekst nie mieści się na grafice: {text!r}")


def _center(draw, box, text, font, fill):
    x1, y1, x2, y2 = box
    b = draw.textbbox((0, 0), text, font=font)
    tw, th = b[2] - b[0], b[3] - b[1]
    if tw > x2 - x1 or th > y2 - y1:
        raise ValueError(f"Tekst wychodzi poza pole grafiki: {text!r}")
    draw.text((round(x1 + (x2-x1-tw)/2), round(y1 + (y2-y1-th)/2-b[1])),
              text, font=font, fill=fill)


def _money(value):
    return f"{float(value):.2f}".replace(".", ",") + " zł"


def _ball(draw, cx, cy, radius):
    draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius),
                 fill=(247, 249, 252), outline=BLUE, width=4)
    draw.ellipse((cx-radius*.24, cy-radius*.24, cx+radius*.24, cy+radius*.24),
                 fill=NAVY)
    for angle in (0, 72, 144, 216, 288):
        a = math.radians(angle)
        x, y = cx + math.cos(a)*radius*.62, cy + math.sin(a)*radius*.62
        draw.polygon([(x, y-radius*.16), (x+radius*.15, y-radius*.05),
                      (x+radius*.09, y+radius*.14), (x-radius*.09, y+radius*.14),
                      (x-radius*.15, y-radius*.05)], fill=NAVY)


def _fresh_canvas():
    image = Image.new("RGB", (W, H), NAVY)
    px = image.load()
    # Smooth vertical navy-to-blue background, generated from scratch.
    for y in range(H):
        t = y / (H - 1)
        c = tuple(round(NAVY[i] * (1-t) + NAVY2[i] * t) for i in range(3))
        for x in range(W):
            px[x, y] = c
    d = ImageDraw.Draw(image)
    # Subtle diagonal stadium-light accents.
    for x in range(-H, W, 90):
        d.line((x, 0, x + H, H), fill=(13, 54, 105), width=2)
    # Header panel and original-brand-inspired crown/eye mark, all drawn anew.
    d.rounded_rectangle((48, 38, 976, 238), radius=34, fill=(3, 15, 36),
                        outline=(31, 126, 218), width=4)
    # Crown
    d.polygon([(112, 130), (126, 75), (157, 111), (185, 63),
               (213, 111), (244, 75), (257, 130)], fill=GREEN,
              outline=WHITE)
    d.rectangle((119, 132, 250, 148), fill=GREEN)
    # Eye mark
    d.arc((130, 153, 246, 218), 195, 345, fill=WHITE, width=5)
    d.arc((130, 153, 246, 218), 15, 165, fill=WHITE, width=5)
    d.ellipse((178, 170, 198, 190), fill=GREEN)
    d.text((292, 66), "OKO TYPERA", font=_font(66), fill=WHITE)
    d.text((296, 145), "ROGI FT  •  DUBEL", font=_font(36), fill=GREEN)
    _ball(d, 870, 136, 58)
    # Main title band
    d.rounded_rectangle((82, 270, 942, 350), radius=24, fill=BLUE)
    _center(d, (100, 278, 924, 340), "DWA MECZE  •  OVER RZUTÓW ROŻNYCH",
            _fit(d, "DWA MECZE  •  OVER RZUTÓW ROŻNYCH", 790, 36, 22), WHITE)
    # Empty match cards: no baked-in labels or text.
    for y1, y2 in ((382, 555), (575, 748)):
        d.rounded_rectangle((74, y1, 950, y2), radius=28,
                            fill=PALE, outline=(116, 190, 255), width=4)
        d.rounded_rectangle((92, y1+18, 103, y2-18), radius=5, fill=BLUE)
        d.line((735, y1+22, 735, y2-22), fill=(172, 190, 211), width=3)
        _ball(d, 154, (y1+y2)//2, 32)
    # Financial table
    d.rounded_rectangle((74, 778, 950, 1080), radius=28,
                        fill=PALE, outline=(116, 190, 255), width=4)
    for y in (853, 928, 1003):
        d.line((110, y, 914, y), fill=(184, 198, 215), width=2)
    # payout/profit panels
    d.rounded_rectangle((74, 1110, 486, 1302), radius=28,
                        fill=(4, 31, 72), outline=BLUE, width=4)
    d.rounded_rectangle((510, 1110, 950, 1302), radius=28,
                        fill=GREEN, outline=(211, 255, 164), width=4)
    # Footer pitch motif
    d.rounded_rectangle((74, 1330, 950, 1494), radius=24,
                        fill=(5, 39, 56), outline=(32, 151, 118), width=3)
    d.rectangle((110, 1355, 914, 1468), outline=(65, 160, 131), width=3)
    d.line((512, 1355, 512, 1468), fill=(65, 160, 131), width=3)
    d.ellipse((457, 1382, 567, 1442), outline=(65, 160, 131), width=3)
    d.rectangle((110, 1380, 220, 1442), outline=(65, 160, 131), width=3)
    d.rectangle((804, 1380, 914, 1442), outline=(65, 160, 131), width=3)
    _center(d, (120, 1460, 904, 1490), "ANALIZA KURSÓW  •  ROŻNE FT",
            _font(20), (195, 220, 242))
    return image


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12,
                        watermark_id=None, settled=False):
    """Draw the entire poster from scratch using only the current coupon data."""
    if len(legs) != 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch meczów")
    normalized = []
    for leg in legs:
        home, away = str(leg.get("home") or "").strip(), str(leg.get("away") or "").strip()
        if not home or not away or home.lower() in {"a", "b", "mecz a", "mecz b", "team a", "team b"} or away.lower() in {"a", "b", "mecz a", "mecz b", "team a", "team b"}:
            raise ValueError("Brak prawdziwej nazwy jednej z drużyn")
        line, odds = float(leg.get("line", 0)), float(leg.get("odds", 0))
        if not 8.5 <= line <= 9.5:
            raise ValueError("Blokada publikacji: dozwolone wyłącznie OVER 8.5–9.5 rożnych FT")
        if not 1.60 <= odds <= 2.20:
            raise ValueError("Blokada publikacji: kurs poza zakresem 1.60–2.20")
        normalized.append((home, away, line, odds))
    stake, total_odds, tax_rate = float(stake), float(total_odds), float(tax_rate)
    if stake <= 0 or total_odds <= 0 or not 0 <= tax_rate < 1:
        raise ValueError("Nieprawidłowa stawka, kurs łączny lub podatek")
    if abs(normalized[0][3] * normalized[1][3] - total_odds) > 0.02:
        raise ValueError("Kurs łączny nie zgadza się z kursami obu meczów")

    image = _fresh_canvas()
    d = ImageDraw.Draw(image)
    for (home, away, line, odds), (top, bottom) in zip(normalized, ((382, 555), (575, 748))):
        fixture = f"{home}  –  {away}"
        _center(d, (190, top+20, 715, top+92), fixture,
                _fit(d, fixture, 505, 35, 17), DARK)
        market = f"OVER {line:.1f} rożnych FT"
        _center(d, (190, top+99, 715, top+155), market,
                _fit(d, market, 505, 31, 20), BLUE)
        _center(d, (750, top+24, 930, top+75), "KURS", _font(24), DARK)
        _center(d, (750, top+77, 930, top+145), f"{odds:.2f}",
                _font(54), BLUE)

    tax = round(stake * tax_rate, 2)
    playable = round(stake-tax, 2)
    payout = round(playable*total_odds, 2)
    profit = round(payout-stake, 2)
    rows = (
        ("KURS ŁĄCZNY", f"{total_odds:.2f}", BLUE),
        ("STAWKA", _money(stake), DARK),
        (f"PODATEK {tax_rate*100:.0f}%", _money(tax), RED),
        ("KWOTA GRAJĄCA", _money(playable), DARK),
    )
    for i, (label, value, color) in enumerate(rows):
        y = 790 + i*73
        _center(d, (112, y, 560, y+65), label, _font(29), DARK)
        _center(d, (580, y, 910, y+65), value, _font(34), color)
    _center(d, (96, 1125, 464, 1185), "WYPŁATA", _font(34), WHITE)
    _center(d, (96, 1190, 464, 1278), _money(payout),
            _fit(d, _money(payout), 340, 47, 27), WHITE)
    _center(d, (532, 1125, 928, 1185), "ZYSK NETTO", _font(34), DARK)
    profit_text = ("+" if profit >= 0 else "") + _money(profit)
    _center(d, (532, 1190, 928, 1278), profit_text,
            _fit(d, profit_text, 370, 47, 25), DARK)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
