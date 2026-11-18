"""Professional FT-corners double poster rendered on the original Oko Typera artwork."""
from io import BytesIO
from pathlib import Path
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BASE_IMAGE = Path(__file__).resolve().with_name("Screenshot_20261117-110724.png")
W, H = 1024, 1536
NAVY = (8, 24, 52)
BLUE = (0, 105, 205)
RED = (190, 20, 20)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


@lru_cache(maxsize=1)
def _clean_master():
    """Build a clean, textured data layer from the supplied original poster."""
    if not BASE_IMAGE.is_file():
        raise FileNotFoundError(f"Brak oryginalnej grafiki: {BASE_IMAGE}")
    image = Image.open(BASE_IMAGE).convert("RGB")
    if image.size != (W, H):
        image = image.resize((W, H), Image.Resampling.LANCZOS)
    rng = np.random.default_rng(21)

    def paper(width, height, strength=3.0):
        coarse = rng.normal(0, 1, (max(2, height // 24), max(2, width // 24))).astype("float32")
        coarse_image = Image.fromarray(np.uint8(np.clip(127.5 + coarse * 24, 0, 255))).resize(
            (width, height), Image.Resampling.BICUBIC
        )
        low = (np.asarray(coarse_image).astype("float32") - 127.5) / 127.5 * 1.5
        grain = rng.normal(0, strength, (height, width)).astype("float32") + low
        pixels = np.empty((height, width, 3), dtype=np.uint8)
        for channel, base in enumerate((249, 246, 238)):
            pixels[:, :, channel] = np.clip(base + grain, 0, 255)
        return Image.fromarray(pixels, "RGB")

    for box in ((200, 386, 997, 529), (200, 542, 997, 689)):
        x1, y1, x2, y2 = box
        texture = paper(x2 - x1, y2 - y1, 3.2)
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=30, fill=255)
        image.paste(texture, (x1, y1), mask.crop(box))

    image.paste(paper(790, 286, 3.0), (185, 700))
    draw = ImageDraw.Draw(image)
    for y in (772, 844, 914):
        draw.line((190, y, 970, y), fill=(90, 105, 120), width=2)
    # Restore the original blue separators in both match cards.
    for y1, y2 in ((401, 511), (557, 671)):
        draw.rounded_rectangle((205, y1, 212, y2), radius=3, fill=BLUE)
        draw.rounded_rectangle((770, y1, 777, y2), radius=3, fill=BLUE)

    navy_noise = rng.normal(0, 2.0, (180, 360))
    navy = np.empty((180, 360, 3), dtype=np.uint8)
    for channel, base in enumerate((7, 28, 62)):
        navy[:, :, channel] = np.clip(base + navy_noise, 0, 255)
    image.paste(Image.fromarray(navy), (190, 1000))
    green_noise = rng.normal(0, 3.0, (180, 410))
    green = np.empty((180, 410, 3), dtype=np.uint8)
    for channel, base in enumerate((126, 255, 0)):
        green[:, :, channel] = np.clip(base + green_noise, 0, 255)
    image.paste(Image.fromarray(green), (560, 1000))
    return image


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


def _fit(draw, text, max_width, start=38, minimum=18):
    for size in range(start, minimum - 1, -1):
        font = _font(size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    font = _font(minimum)
    if draw.textbbox((0, 0), text, font=font)[2] > max_width:
        raise ValueError(f"Tekst nie mieści się w polu plakatu: {text!r}")
    return font


def _center(draw, box, text, font, fill):
    x1, y1, x2, y2 = box
    bounds = draw.textbbox((0, 0), text, font=font)
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    if width > x2 - x1 or height > y2 - y1:
        raise ValueError(f"Tekst wychodzi poza pole plakatu: {text!r}")
    x = x1 + (x2 - x1 - width) / 2
    y = y1 + (y2 - y1 - height) / 2 - bounds[1]
    draw.text((round(x), round(y)), text, font=font, fill=fill)


def _money(value):
    return f"{float(value):.2f}".replace(".", ",") + " zł"


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12,
                        watermark_id=None, settled=False):
    """Render two real selections and all coupon calculations on a clean master."""
    if len(legs) != 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch meczów")

    normalized = []
    for leg in legs:
        home = str(leg.get("home") or "").strip()
        away = str(leg.get("away") or "").strip()
        if not home or not away:
            raise ValueError("Brak prawdziwej nazwy jednej z drużyn")
        line, odds = float(leg.get("line", 0)), float(leg.get("odds", 0))
        if line < 9.5:
            raise ValueError("Blokada publikacji: rożne FT poniżej OVER 9.5")
        if not 1.70 <= odds <= 3.50:
            raise ValueError("Blokada publikacji: kurs poza zakresem 1.70–3.50")
        normalized.append((home, away, line, odds))

    stake, total_odds, tax_rate = float(stake), float(total_odds), float(tax_rate)
    if stake <= 0 or total_odds <= 0 or not 0 <= tax_rate < 1:
        raise ValueError("Nieprawidłowa stawka, kurs łączny lub podatek")
    calculated_odds = normalized[0][3] * normalized[1][3]
    if abs(calculated_odds - total_odds) > 0.02:
        raise ValueError("Kurs łączny nie zgadza się z kursami obu meczów")

    image = _clean_master().copy()
    draw = ImageDraw.Draw(image)
    card_boxes = ((389, 526), (545, 686))
    for (home, away, line, odds), (top, bottom) in zip(normalized, card_boxes):
        fixture = f"{home} – {away}"
        _center(draw, (225, top + 7, 758, top + 66), fixture,
                _fit(draw, fixture, 515, 38, 14), NAVY)
        market = f"Over {line:.1f} rożnych FT"
        _center(draw, (225, top + 69, 758, bottom - 6), market,
                _fit(draw, market, 515, 36, 20), NAVY)
        _center(draw, (782, top + 7, 990, top + 58), "Kurs", _font(27), NAVY)
        odds_text = f"{odds:.2f}"
        _center(draw, (782, top + 55, 990, bottom - 6), odds_text,
                _fit(draw, odds_text, 195, 60, 38), BLUE)

    tax_amount = round(stake * tax_rate, 2)
    playable_stake = round(stake - tax_amount, 2)
    payout = round(playable_stake * total_odds, 2)
    net_profit = round(payout - stake, 2)
    rows = (
        ("Stawka:", _money(stake), NAVY),
        (f"Podatek {tax_rate * 100:.0f}%:", _money(tax_amount), RED),
        ("Kwota grająca:", _money(playable_stake), NAVY),
        ("Kurs łączny:", f"{total_odds:.2f}", BLUE),
    )
    for index, (label, value, color) in enumerate(rows):
        y = 704 + index * 72
        _center(draw, (190, y, 650, y + 67), label,
                _fit(draw, label, 440, 39, 26), NAVY)
        _center(draw, (660, y, 970, y + 67), value,
                _fit(draw, value, 300, 43, 26), color)

    _center(draw, (205, 1008, 545, 1075), "Wypłata:", _font(39), WHITE)
    payout_text = _money(payout)
    _center(draw, (205, 1070, 545, 1172), payout_text,
            _fit(draw, payout_text, 330, 48, 27), WHITE)
    _center(draw, (575, 1008, 965, 1075), "Zysk netto:", _font(39), BLACK)
    profit_text = ("+" if net_profit >= 0 else "") + _money(net_profit)
    _center(draw, (575, 1070, 965, 1172), profit_text,
            _fit(draw, profit_text, 380, 48, 25), BLACK)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
