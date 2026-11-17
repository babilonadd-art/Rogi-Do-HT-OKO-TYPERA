"""Renderer uses the original OKO TYPERA poster stored in the repository."""
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

TEMPLATE = Path(__file__).with_name("Screenshot_20261117-110724.png")
CREAM = (250, 248, 245)
INK = (8, 24, 52)
BLUE = (0, 105, 205)
RED = (205, 22, 18)


def _font(size, bold=False, italic=False):
    name = "DejaVuSans-BoldOblique.ttf" if italic else ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/" + name, size)
    except OSError:
        return ImageFont.load_default()


def _fit(draw, text, width, size, bold=True, italic=False):
    while size > 11 and draw.textbbox((0, 0), text, font=_font(size, bold, italic))[2] > width:
        size -= 2
    return _font(size, bold, italic)


def _right(draw, x, y, text, font, fill):
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((x - box[2], y), text, font=font, fill=fill)


def _center(draw, x, y, text, font, fill):
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((x - box[2] / 2, y), text, font=font, fill=fill)


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12, watermark_id=None):
    if len(legs) < 2:
        raise ValueError("Grafika dubla wymaga dwóch prawdziwych meczów")
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"Brak oryginalnego szablonu: {TEMPLATE}")
    base = Image.open(TEMPLATE).copy()
    overlay = Image.new("RGBA", (1024, 1536), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # Większy odstęp i mniejsze fonty: nazwa meczu oraz rynek nie mogą się stykać.
    for i, leg in enumerate(legs[:2]):
        top = 386 + i * 155
        d.rectangle((225, top, 765, top + 143), fill=CREAM)
        d.rectangle((775, top, 975, top + 143), fill=CREAM)
        match = f"{str(leg['home']).strip()} – {str(leg['away']).strip()}"
        d.text((235, top + 17), match, font=_fit(d, match, 525, 34), fill=INK)
        market = f"Over {float(leg['line']):.1f} rożnych FT"
        d.text((235, top + 86), market, font=_fit(d, market, 525, 28, False, True), fill=INK)
        d.text((801, top + 20), "Kurs", font=_font(23, True, True), fill=INK)
        odds = f"{float(leg['odds']):.2f}"
        d.text((801, top + 57), odds, font=_fit(d, odds, 160, 49), fill=BLUE)

    tax = round(float(stake) * float(tax_rate), 2)
    playing = round(float(stake) - tax, 2)
    displayed_odds = round(float(total_odds), 2)
    payout = round(playing * displayed_odds, 2)
    profit = round(payout - float(stake), 2)

    def pl_number(value):
        return f"{float(value):.2f}".replace(".", ",")

    # Cały obszar trzeciego, nieużywanego wiersza „Mecz C” oraz panelu
    # podsumowania czyścimy jednym prostokątem. Poprzednio pozostawał
    # wąski pasek starego wiersza nad panelem i po jego lewej stronie.
    d.rectangle((61, 690, 970, 990), fill=CREAM)

    # Kolejność zgodnie z prośbą: kurs, stawka, podatek, kwota grająca.
    # Podsumowanie rysujemy ponownie na wyczyszczonym obszarze.
    rows = [
        ("Kurs łączny:", pl_number(displayed_odds), BLUE),
        ("Stawka:", f"{pl_number(stake)} zł", INK),
        ("Podatek 12%:", f"{pl_number(tax)} zł", RED),
        ("Kwota grająca:", f"{pl_number(playing)} zł", INK),
    ]
    for y, (label, value, color) in zip((713, 785, 857, 929), rows):
        d.text((202, y), label, font=_font(34, True), fill=INK)
        _right(d, 952, y, value, _fit(d, value, 240, 37), color)
    for y in (774, 846, 918, 990):
        d.line((61, y, 950, y), fill=(120, 126, 133), width=2)

    # Wygrana na końcu: wypłata i zysk netto w dolnych polach.
    d.rectangle((205, 1018, 553, 1167), fill=(7, 27, 58))
    d.rectangle((572, 1018, 971, 1167), fill=(110, 255, 0))
    d.text((239, 1027), "Wygrana / wypłata:", font=_fit(d, "Wygrana / wypłata:", 300, 34), fill=(255, 255, 255))
    payout_text = f"{pl_number(payout)} zł"
    d.text((224, 1080), payout_text, font=_fit(d, payout_text, 310, 48), fill=(255, 255, 255))
    _center(d, 770, 1027, "Zysk netto:", _font(34, True, True), (0, 0, 0))
    profit_text = f"{profit:+.2f}".replace(".", ",") + " zł"
    _center(d, 770, 1080, profit_text, _fit(d, profit_text, 360, 48, True, True), (0, 0, 0))

    overlay = overlay.resize(base.size, Image.Resampling.LANCZOS)
    im = Image.alpha_composite(base.convert("RGBA"), overlay).convert(base.mode)
    out = BytesIO()
    im.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out
