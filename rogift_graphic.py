from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math
import os

W, H = 1080, 1350
NAVY = (5, 24, 53)
BLUE = (0, 105, 205)
PAPER = (247, 244, 235)
INK = (8, 20, 42)
GREEN = (155, 255, 0)
RED = (190, 20, 20)


def _font(size, bold=False, italic=False):
    candidates = []
    if bold and italic:
        candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"]
    elif bold:
        candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    elif italic:
        candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"]
    candidates += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _fit(draw, text, max_width, size, bold=False):
    font = _font(size, bold=bold)
    while size > 20 and draw.textbbox((0, 0), text, font=font)[2] > max_width:
        size -= 2
        font = _font(size, bold=bold)
    return font


def _pl(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12):
    """Return a polished PNG BytesIO for a two-leg FT corners double."""
    img = Image.new("RGB", (W, H), PAPER)
    px = img.load()
    for y in range(H):
        shade = int(248 - 13 * y / H)
        for x in range(W):
            grain = ((x * 17 + y * 31) % 7) - 3
            px[x, y] = (max(0, shade + grain), max(0, shade - 2 + grain), max(0, shade - 10 + grain))

    d = ImageDraw.Draw(img)
    # Dark stadium header with floodlights.
    d.rectangle((0, 0, W, 360), fill=NAVY)
    for i in range(34):
        x = 26 + i * 31
        y = 20 + int(35 * math.sin(i / 4))
        d.ellipse((x, y, x + 9, y + 9), fill=(220, 241, 255))
    d.ellipse((720, -210, 1190, 280), fill=(17, 65, 111))
    d.ellipse((800, -160, 1140, 180), fill=(25, 87, 143))
    # Branding
    d.text((42, 35), "♛", font=_font(74, bold=True), fill=(255, 194, 35))
    d.text((45, 118), "OKO", font=_font(58, bold=True), fill=(255, 255, 255))
    d.text((45, 177), "TYPERA", font=_font(54, bold=True), fill=(0, 145, 255))
    d.line((285, 35, 285, 255), fill=(255, 255, 255), width=4)
    d.text((330, 48), "ROGI FT", font=_font(100, bold=True), fill=(255, 255, 255), stroke_width=2, stroke_fill=(4, 16, 36))
    d.text((350, 165), "DUBEL", font=_font(70, bold=True), fill=(255, 255, 255))
    d.line((350, 252, 850, 230), fill=BLUE, width=10)

    # Match panels
    y_positions = [390, 585]
    for idx, leg in enumerate(legs[:2]):
        y = y_positions[idx]
        d.rounded_rectangle((34, y, 1046, y + 164), radius=30, fill=(255, 255, 250), outline=(210, 215, 220), width=3)
        d.rounded_rectangle((50, y + 18, 190, y + 146), radius=18, fill=NAVY)
        # football icon
        cx, cy = 120, y + 82
        d.ellipse((75, cy - 43, 165, cy + 47), fill=(245, 245, 245), outline=INK, width=5)
        d.polygon([(120,cy-24),(139,cy-10),(132,cy+12),(108,cy+12),(101,cy-10)], fill=INK)
        d.line((120,cy-24,120,cy-42), fill=INK, width=4)
        d.line((101,cy-10,82,cy-20), fill=INK, width=4)
        d.line((139,cy-10,158,cy-20), fill=INK, width=4)
        d.line((108,cy+12,96,cy+34), fill=INK, width=4)
        d.line((132,cy+12,144,cy+34), fill=INK, width=4)
        d.line((210, y + 20, 210, y + 144), fill=BLUE, width=5)
        match = f"{leg['home']} – {leg['away']}"
        d.text((240, y + 22), match, font=_fit(d, match, 520, 43, bold=True), fill=INK)
        market = f"Over {leg['line']:.1f} rożnych FT"
        d.text((240, y + 88), market, font=_fit(d, market, 520, 39), fill=INK)
        d.line((790, y + 18, 790, y + 146), fill=BLUE, width=4)
        d.text((824, y + 24), "Kurs", font=_font(28, bold=True), fill=INK)
        odd = f"{float(leg['odds']):.2f}"
        d.text((820, y + 60), odd, font=_fit(d, odd, 195, 66, bold=True), fill=BLUE)

    tax = round(stake * tax_rate, 2)
    playing = round(stake - tax, 2)
    payout = round(playing * float(total_odds), 2)
    profit = round(payout - stake, 2)
    rows = [
        ("Stawka:", f"{_pl(stake)} zł", INK),
        (f"Podatek {tax_rate*100:.0f}%:", f"{_pl(tax)} zł", RED),
        ("Kwota grająca:", f"{_pl(playing)} zł", INK),
        ("Kurs łączny:", f"{float(total_odds):.2f}", BLUE),
    ]
    y = 790
    for label, value, color in rows:
        d.text((205, y), label, font=_font(39, bold=True), fill=INK)
        f = _fit(d, value, 380, 43, bold=True)
        box = d.textbbox((0, 0), value, font=f)
        d.text((1005 - (box[2] - box[0]), y), value, font=f, fill=color)
        d.line((68, y + 65, 1010, y + 65), fill=(150, 158, 170), width=2)
        y += 78

    # Payout / net profit banners
    d.rounded_rectangle((34, 1110, 535, 1245), radius=22, fill=NAVY)
    d.text((72, 1126), "Wypłata:", font=_font(33, bold=True), fill=(255, 255, 255))
    payout_text = f"{_pl(payout)} zł"
    d.text((72, 1170), payout_text, font=_fit(d, payout_text, 420, 57, bold=True), fill=(255, 255, 255))
    d.rounded_rectangle((555, 1110, 1046, 1245), radius=22, fill=GREEN)
    d.text((590, 1126), "Zysk netto:", font=_font(33, bold=True), fill=(0, 0, 0))
    profit_text = f"{profit:+,.2f} zł".replace(",", "X").replace(".", ",").replace("X", " ")
    d.text((590, 1170), profit_text, font=_fit(d, profit_text, 420, 57, bold=True), fill=(0, 0, 0))
    d.text((48, 1280), "Przykład — zasady podatku zależą od bukmachera", font=_font(22, bold=True), fill=INK)
    d.text((770, 1280), f"DUBEL {coupon_no}", font=_font(22, bold=True), fill=BLUE)

    # Subtle blue pitch lines in footer.
    d.line((0, 1335, 1080, 1300), fill=BLUE, width=7)
    d.line((780, 1350, 930, 1288), fill=NAVY, width=4)
    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out
