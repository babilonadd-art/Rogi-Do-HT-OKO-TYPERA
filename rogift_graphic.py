"""Renderer dubla FT oparty na oryginalnym plakacie Oko Typera.

Tło (Screenshot_20261117-110724.png) pozostaje oryginalne. Nadpisywane są
wyłącznie dane zmienne: dwa mecze, kursy, stawka i wyliczenia kuponu.
"""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE_IMAGE = Path(__file__).resolve().with_name("Screenshot_20261117-110724.png")
W, H = 1024, 1536
NAVY = (8, 24, 52)
BLUE = (0, 105, 205)
RED = (190, 20, 20)
CREAM = (250, 247, 240)
GREEN = (126, 255, 0)
WHITE = (255, 255, 255)


def _font(size, bold=True):
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(draw, text, max_width, start=36, minimum=18):
    for size in range(start, minimum - 1, -1):
        font = _font(size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return _font(minimum)


def _center(draw, box, text, font, fill):
    x1, y1, x2, y2 = box
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    x = x1 + (x2 - x1 - width) / 2
    y = y1 + (y2 - y1 - height) / 2 - bounds[1]
    draw.text((x, y), text, font=font, fill=fill)


def _money(value):
    return f"{float(value):.2f}".replace(".", ",") + " zł"


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12,
                        watermark_id=None, settled=False):
    """Renderuje dubel na oryginalnej grafice, z łącznym kursem i stawką."""
    if len(legs) != 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch meczów")
    if not BASE_IMAGE.is_file():
        raise FileNotFoundError(f"Brak oryginalnej grafiki: {BASE_IMAGE}")

    normalized = []
    for leg in legs:
        home = str(leg.get("home") or "").strip()
        away = str(leg.get("away") or "").strip()
        if not home or not away:
            raise ValueError("Brak prawdziwej nazwy jednej z drużyn")
        line = float(leg.get("line", 0))
        odds = float(leg.get("odds", 0))
        if line < 9.5:
            raise ValueError("Blokada publikacji: rożne FT poniżej OVER 9.5")
        if not 1.70 <= odds <= 3.50:
            raise ValueError("Blokada publikacji: kurs poza zakresem 1.70–3.50")
        normalized.append((home, away, line, odds))

    stake = float(stake)
    total_odds = float(total_odds)
    tax_rate = float(tax_rate)
    if stake <= 0 or total_odds <= 0 or not 0 <= tax_rate < 1:
        raise ValueError("Nieprawidłowa stawka, kurs łączny lub podatek")

    image = Image.open(BASE_IMAGE).convert("RGB")
    if image.size != (W, H):
        image = image.resize((W, H), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)

    # Dwa jasne pola meczowe: zachowujemy logo piłek, obramowanie i niebieskie
    # separatory z oryginału, wymieniamy tylko tekst meczów i kursy.
    row_boxes = ((394, 518), (548, 674))
    for (home, away, line, odds), (top, bottom) in zip(normalized, row_boxes):
        draw.rectangle((222, top, 768, bottom), fill=CREAM)
        draw.rectangle((782, top, 982, bottom), fill=CREAM)

        fixture = f"{home} – {away}"
        draw.text((237, top + 13), fixture,
                  font=_fit(draw, fixture, 515, 34, 19), fill=NAVY)

        y_market = top + 69
        market_font = _font(31)
        number_font = _font(33)
        draw.text((237, y_market), "Over", font=market_font, fill=NAVY)
        x = 237 + draw.textbbox((0, 0), "Over ", font=market_font)[2]
        line_text = f"{line:.1f}"
        draw.text((x, y_market), line_text, font=number_font, fill=BLUE)
        x += draw.textbbox((0, 0), line_text, font=number_font)[2]
        draw.text((x, y_market), " rożnych FT", font=market_font, fill=NAVY)

        _center(draw, (788, top + 8, 975, top + 48), "Kurs", _font(27), NAVY)
        _center(draw, (788, top + 45, 975, bottom - 8),
                f"{odds:.2f}", _font(56), BLUE)

    # Podsumowanie kuponu według układu z oryginalnego plakatu.
    tax_amount = stake * tax_rate
    playable_stake = stake - tax_amount
    payout = playable_stake * total_odds
    net_profit = payout - stake

    financial_rows = (
        (705, 768, _money(stake), NAVY),
        (775, 840, _money(tax_amount), RED),
        (850, 910, _money(playable_stake), NAVY),
        (916, 981, f"{total_odds:.2f}", BLUE),
    )
    for top, bottom, value, color in financial_rows:
        draw.rectangle((735, top, 970, bottom), fill=CREAM)
        font = _fit(draw, value, 225, 47, 30)
        bounds = draw.textbbox((0, 0), value, font=font)
        draw.text((966 - (bounds[2] - bounds[0]), top + 2),
                  value, font=font, fill=color)

    # Nadpisujemy wyłącznie liczby w polach wypłaty i zysku netto.
    draw.rectangle((205, 1072, 545, 1159), fill=(7, 28, 62))
    _center(draw, (208, 1072, 540, 1159), _money(payout), _font(52), WHITE)
    draw.rectangle((565, 1072, 970, 1159), fill=GREEN)
    _center(draw, (570, 1072, 968, 1159),
            "+" + _money(net_profit), _fit(draw, "+" + _money(net_profit), 390, 51, 30),
            (0, 0, 0))

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
