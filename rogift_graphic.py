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

    # Czyścimy całe karty, nie tylko same linie tekstu. W poprzedniej
    # wersji zostawały fragmenty starego nadruku przy krawędziach pól.
    row_boxes = ((378, 535), (534, 696))
    for (leg, (top, bottom)) in zip(normalized, row_boxes):
        home, away, line, odds = leg
        draw.rectangle((202, top, 1005, bottom), fill=CREAM)

        # Odtworzenie separatorów oryginalnej karty.
        draw.rectangle((207, top + 17, 212, bottom - 17), fill=BLUE)
        draw.rectangle((770, top + 17, 775, bottom - 17), fill=BLUE)

        fixture = f"{home} – {away}"
        fixture_font = _fit(draw, fixture, 515, 42, 20)
        _center(draw, (225, top + 10, 758, top + 82), fixture, fixture_font, NAVY)

        market = f"Over {line:.1f} rożnych FT"
        market_font = _fit(draw, market, 515, 36, 20)
        _center(draw, (225, top + 82, 758, bottom - 8), market, market_font, NAVY)

        _center(draw, (782, top + 10, 990, top + 68), "Kurs", _font(29), NAVY)
        odds_text = f"{odds:.2f}"
        _center(draw, (782, top + 66, 990, bottom - 8),
                odds_text, _fit(draw, odds_text, 195, 62, 38), BLUE)

    tax_amount = stake * tax_rate
    playable_stake = stake - tax_amount
    payout = playable_stake * total_odds
    net_profit = payout - stake

    # Cały blok finansowy jest odtwarzany od czystego tła.
    draw.rectangle((185, 690, 985, 992), fill=CREAM)
    financial_rows = (
        (695, 769, "Stawka:", _money(stake), NAVY),
        (770, 844, f"Podatek {tax_rate * 100:.0f}%:", _money(tax_amount), RED),
        (845, 919, "Kwota grająca:", _money(playable_stake), NAVY),
        (920, 989, "Kurs łączny:", f"{total_odds:.2f}", BLUE),
    )
    for top, bottom, label, value, color in financial_rows:
        _center(draw, (195, top, 650, bottom), label, _fit(draw, label, 440, 42, 28), NAVY)
        _center(draw, (660, top, 972, bottom), value, _fit(draw, value, 300, 48, 28), color)

    # Czyścimy tekst w obu panelach wyniku z zapasem, by nie zostawić
    # żadnych resztek poprzednich cyfr ani liter.
    draw.rectangle((195, 992, 552, 1180), fill=(7, 28, 62))
    draw.rectangle((565, 992, 972, 1180), fill=GREEN)
    _center(draw, (205, 997, 545, 1074), "Wypłata:", _font(42), WHITE)
    payout_text = _money(payout)
    _center(draw, (205, 1070, 545, 1172), payout_text,
            _fit(draw, payout_text, 335, 53, 30), WHITE)
    _center(draw, (575, 997, 965, 1074), "Zysk netto:", _font(42), (0, 0, 0))
    profit_text = ("+" if net_profit >= 0 else "") + _money(net_profit)
    _center(draw, (575, 1070, 965, 1172), profit_text,
            _fit(draw, profit_text, 375, 53, 28), (0, 0, 0))

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
