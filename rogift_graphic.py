"""Render FT corner doubles on the repository poster without overlapping text."""
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Keep the existing repository asset as the source of truth.
TEMPLATE = Path(__file__).with_name("Screenshot_20261117-110724.png")
CREAM = (248, 246, 237)
INK = (8, 24, 52)
BLUE = (0, 105, 205)
RED = (205, 22, 18)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def _font(size, bold=False, italic=False):
    name = "DejaVuSans-BoldOblique.ttf" if italic else ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/" + name, max(12, int(size)))
    except OSError:
        return ImageFont.load_default()


def _fit(draw, text, max_width, max_height, size, bold=True, italic=False):
    while size > 12:
        f = _font(size, bold, italic)
        box = draw.textbbox((0, 0), text, font=f)
        if box[2] - box[0] <= max_width and box[3] - box[1] <= max_height:
            return f
        size -= 1
    return _font(12, bold, italic)


def _draw_centered(draw, box, text, font, fill, align="left"):
    x0, y0, x1, y1 = box
    b = draw.textbbox((0, 0), text, font=font)
    tw, th = b[2] - b[0], b[3] - b[1]
    x = x0 if align == "left" else (x1 - tw if align == "right" else x0 + (x1 - x0 - tw) / 2)
    y = y0 + (y1 - y0 - th) / 2 - b[1]
    draw.text((int(x), int(y)), text, font=font, fill=fill)


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12, watermark_id=None):
    if len(legs) < 2:
        raise ValueError("Grafika dubla wymaga dwóch prawdziwych meczów")
    for leg in legs[:2]:
        if not str(leg.get("home", "")).strip() or not str(leg.get("away", "")).strip():
            raise ValueError("Brak prawdziwych nazw drużyn — nie generuję grafiki")
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"Brak szablonu: {TEMPLATE}")

    base = Image.open(TEMPLATE).convert("RGB")
    w, h = base.size
    # Wszystkie pozycje są procentowe względem plakatu, więc nie rozjeżdżają
    # się przy innym rozmiarze pliku lub miniaturze.
    sx, sy = w / 1024.0, h / 1536.0
    def B(x0, y0, x1, y1):
        return (round(x0*sx), round(y0*sy), round(x1*sx), round(y1*sy))
    def fs(n):
        return max(12, round(n * min(sx, sy)))

    d = ImageDraw.Draw(base)

    # Dwa wiersze meczowe: osobne, niekolidujące pola na nazwę, rynek i kurs.
    for i, leg in enumerate(legs[:2]):
        y0 = 389 + i * 155
        # Wyczyść wyłącznie tekst, nie piłkę ani pionowe separatory.
        match_box = B(228, y0, 765, y0 + 58)
        market_box = B(228, y0 + 57, 765, y0 + 112)
        odds_box = B(786, y0 + 7, 972, y0 + 112)
        d.rectangle(match_box, fill=CREAM)
        d.rectangle(market_box, fill=CREAM)
        d.rectangle(odds_box, fill=CREAM)

        match = f"{str(leg['home']).strip()} – {str(leg['away']).strip()}"
        market = f"Over {float(leg['line']):.1f} rożnych FT"
        _draw_centered(d, match_box, match,
                       _fit(d, match, match_box[2]-match_box[0]-12, match_box[3]-match_box[1]-6, fs(43)),
                       INK)
        _draw_centered(d, market_box, market,
                       _fit(d, market, market_box[2]-market_box[0]-12, market_box[3]-market_box[1]-6, fs(36), False, True),
                       INK)
        _draw_centered(d, B(795, y0+8, 970, y0+45), "Kurs",
                       _font(fs(25), True, True), INK, "center")
        odds_text = f"{float(leg['odds']):.2f}"
        _draw_centered(d, B(790, y0+42, 970, y0+111), odds_text,
                       _fit(d, odds_text, round(170*sx), round(65*sy), fs(59)), BLUE, "center")

    tax = round(float(stake) * float(tax_rate), 2)
    playing = round(float(stake) - tax, 2)
    payout = round(playing * float(total_odds), 2)
    profit = round(payout - float(stake), 2)

    # Podsumowanie: cztery równe wiersze, tekst zawsze wyśrodkowany w swoim pasie.
    d.rectangle(B(190, 704, 974, 990), fill=CREAM)
    rows = [
        ("Kurs łączny:", f"{float(total_odds):.2f}", BLUE),
        ("Podatek 12%:", f"{tax:.2f} zł", RED),
        ("Stawka:", f"{float(stake):.2f} zł", INK),
        ("Kwota grająca:", f"{playing:.2f} zł", INK),
    ]
    for i, (label, value, color) in enumerate(rows):
        y0 = 706 + i * 70
        left_box = B(200, y0, 650, y0+65)
        right_box = B(660, y0, 956, y0+65)
        _draw_centered(d, left_box, label, _fit(d, label, left_box[2]-left_box[0]-8, left_box[3]-left_box[1]-4, fs(36)), INK)
        _draw_centered(d, right_box, value, _fit(d, value, right_box[2]-right_box[0]-8, right_box[3]-right_box[1]-4, fs(39)), color, "right")
        if i < 3:
            yy = round((y0+69)*sy)
            d.line((round(61*sx), yy, round(950*sx), yy), fill=(120, 126, 133), width=max(1, round(2*min(sx, sy))))

    # Panele wypłaty i zysku: osobne prostokąty i dopasowany rozmiar tekstu.
    d.rectangle(B(205, 1018, 553, 1167), fill=(7, 27, 58))
    d.rectangle(B(572, 1018, 971, 1167), fill=(110, 255, 0))
    payout_label = B(220, 1024, 540, 1075)
    payout_value = B(218, 1075, 545, 1158)
    profit_label = B(590, 1024, 955, 1075)
    profit_value = B(585, 1075, 960, 1158)
    _draw_centered(d, payout_label, "Wypłata:", _font(fs(38), True), WHITE, "center")
    payout_text = f"{payout:.2f} zł"
    _draw_centered(d, payout_value, payout_text,
                   _fit(d, payout_text, payout_value[2]-payout_value[0]-10, payout_value[3]-payout_value[1]-6, fs(49), True, True),
                   WHITE, "center")
    _draw_centered(d, profit_label, "Zysk netto:", _font(fs(36), True, True), BLACK, "center")
    profit_text = f"{profit:+.2f} zł"
    _draw_centered(d, profit_value, profit_text,
                   _fit(d, profit_text, profit_value[2]-profit_value[0]-10, profit_value[3]-profit_value[1]-6, fs(49), True, True),
                   BLACK, "center")

    out = BytesIO()
    base.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out
