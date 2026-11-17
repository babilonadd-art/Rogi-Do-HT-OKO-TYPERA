"""Fresh, clean FT-corners double graphic. No inherited placeholder text."""
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

W, H = 1024, 1536
BG = (8, 18, 38)
PANEL = (17, 34, 62)
WHITE = (247, 250, 255)
MUTED = (164, 183, 207)
BLUE = (50, 157, 255)
GREEN = (46, 210, 160)
RED = (255, 105, 105)


def _font(size, bold=False, italic=False):
    if italic:
        paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"]
    elif bold:
        paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    else:
        paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit(draw, text, max_width, size, bold=True):
    while size > 18:
        font = _font(size, bold=bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 2
    return _font(size, bold=bold)


def _center(draw, xy, text, font, fill):
    x, y = xy
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((x - (box[2] - box[0]) / 2, y), text, font=font, fill=fill)


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12,
                        watermark_id=None):
    """Render a fresh poster from a blank canvas; no old placeholder can remain."""
    if len(legs) < 2:
        raise ValueError("Grafika dubla wymaga dwóch zdarzeń")

    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    # Subtle vertical background bands and decorative accents.
    for y in range(H):
        t = y / H
        c = (int(8 + 5*t), int(18 + 8*t), int(38 + 12*t))
        d.line((0, y, W, y), fill=c)
    d.rounded_rectangle((34, 34, 990, 1502), radius=38,
                        outline=(38, 75, 112), width=3)

    _center(d, (W//2, 105), "ROGI FT", _font(76, bold=True), WHITE)
    _center(d, (W//2, 205), "DUBLE • RZUTY ROŻNE", _font(30, bold=True), BLUE)
    d.line((100, 275, 924, 275), fill=(46, 91, 135), width=3)

    for i, leg in enumerate(legs[:2]):
        top = 330 + i * 300
        d.rounded_rectangle((78, top, 946, top+250), radius=28,
                            fill=PANEL, outline=(39, 80, 120), width=3)
        d.rounded_rectangle((78, top, 94, top+250), radius=7, fill=BLUE)
        home = str(leg["home"]).strip()
        away = str(leg["away"]).strip()
        match = f"{home}  –  {away}"
        match_font = _fit(d, match, 790, 43)
        _center(d, (512, top+42), match, match_font, WHITE)

        line = float(leg["line"])
        market = f"OVER {line:.1f} RZUTÓW ROŻNYCH FT"
        _center(d, (512, top+122), market, _font(27, bold=True), MUTED)

        odds = f"KURS  {float(leg['odds']):.2f}"
        d.rounded_rectangle((350, top+174, 674, top+230), radius=18, fill=(14, 74, 125))
        _center(d, (512, top+183), odds, _font(31, bold=True), WHITE)

    d.line((100, 990, 924, 990), fill=(46, 91, 135), width=3)
    _center(d, (512, 1030), "PODSUMOWANIE KUPONU", _font(30, bold=True), BLUE)

    tax = round(float(stake) * float(tax_rate), 2)
    playing = round(float(stake) - tax, 2)
    payout = round(playing * float(total_odds), 2)
    profit = round(payout - float(stake), 2)

    rows = [
        ("STAWKA", f"{float(stake):.2f} zł", WHITE),
        ("PODATEK", f"{tax:.2f} zł", RED),
        ("STAWKA PO PODATKU", f"{playing:.2f} zł", WHITE),
        ("KURS ŁĄCZNY", f"{float(total_odds):.2f}", BLUE),
    ]
    y = 1100
    for label, value, color in rows:
        d.text((130, y), label, font=_font(25, bold=True), fill=MUTED)
        value_font = _fit(d, value, 330, 32)
        d.text((890-d.textbbox((0,0), value, font=value_font)[2],
                y-5), value, font=value_font, fill=color)
        y += 76

    d.rounded_rectangle((92, 1420, 932, 1480), radius=18, fill=(13, 73, 82))
    payout_label = f"MOŻLIWA WYGRANA: {payout:.2f} zł   |   ZYSK: {profit:+.2f} zł"
    _center(d, (512, 1434), payout_label, _fit(d, payout_label, 790, 25), GREEN)

    output = BytesIO()
    im.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
