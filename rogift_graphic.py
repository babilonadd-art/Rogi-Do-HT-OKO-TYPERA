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

    # Wyczyść CAŁE obszary zmiennych napisów. Nie nakładamy nowych liczb
    # na stare napisy z szablonu — to powodowało efekt podwójnego tekstu.
    row_boxes = ((390, 529), (540, 688))
    for (index, ((home, away, line, odds), (top, bottom))) in enumerate(zip(normalized, row_boxes)):
        # Pole meczu od prawego separatora do prawej krawędzi karty.
        draw.rectangle((220, top, 995, bottom), fill=CREAM)
        # Odtworzenie pionowego separatora i separatora kursu.
        draw.rectangle((207, top + 13, 212, bottom - 13), fill=BLUE)
        draw.rectangle((770, top + 13, 775, bottom - 13), fill=BLUE)

        fixture = f"{home} – {away}"
        fixture_font = _fit(draw, fixture, 520, 43, 22)
        _center(draw, (230, top + 8, 758, top + 73), fixture, fixture_font, NAVY)

        market = f"Over {line:.1f} rożnych FT"
        market_font = _fit(draw, market, 520, 38, 23)
        _center(draw, (230, top + 72, 758, bottom - 5), market, market_font, NAVY)

        _center(draw, (782, top + 7, 990, top + 57), "Kurs", _font(29), NAVY)
        _center(draw, (782, top + 53, 990, bottom - 5),
                f"{odds:.2f}", _fit(draw, f"{odds:.2f}", 195, 66, 42), BLUE)

    # Pełne odtworzenie czterech wierszy finansowych — czyścimy także stare
    # etykiety i wartości, aby żaden fragment poprzedniego tekstu nie prześwitywał.
    tax_amount = stake * tax_rate
    playable_stake = stake - tax_amount
    payout = playable_stake * total_odds
    net_profit = payout - stake

    draw.rectangle((190, 697, 978, 985), fill=CREAM)
    financial_rows = (
        (699, 771, "Stawka:", _money(stake), NAVY),
        (772, 843, f"Podatek {tax_rate * 100:.0f}%:", _money(tax_amount), RED),
        (844, 914, "Kwota grająca:", _money(playable_stake), NAVY),
        (915, 984, "Kurs łączny:", f"{total_odds:.2f}", BLUE),
    )
    for top, bottom, label, value, color in financial_rows:
        _center(draw, (195, top, 650, bottom), label, _fit(draw, label, 440, 43, 30), NAVY)
        _center(draw, (660, top, 972, bottom), value, _fit(draw, value, 300, 49, 30), color)

    # Odtwórz oba pola wyniku w oryginalnych panelach. Zostawiamy puchar
    # i zieloną strzałkę z oryginalnej grafiki; czyścimy całe obszary tekstu.
    draw.rectangle((195, 1000, 552, 1170), fill=(7, 28, 62))
    draw.rectangle((565, 1000, 972, 1170), fill=GREEN)
    _center(draw, (205, 1003, 545, 1072), "Wypłata:", _font(43), WHITE)
    _center(draw, (205, 1070, 545, 1165), _money(payout),
            _fit(draw, _money(payout), 335, 55, 34), WHITE)
    _center(draw, (575, 1003, 965, 1072), "Zysk netto:", _font(43), (0, 0, 0))
    profit_text = ("+" if net_profit >= 0 else "") + _money(net_profit)
    _center(draw, (575, 1070, 965, 1165), profit_text,
            _fit(draw, profit_text, 375, 55, 30), (0, 0, 0))

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
