"""Minimalny obraz dubla rożnych FT: tylko dwa mecze, rynek i kursy."""
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

W, H = 900, 430
NAVY = (7, 24, 53)
PANEL = (242, 247, 253)
BLUE = (28, 125, 225)
WHITE = (250, 252, 255)
MUTED = (92, 112, 140)


def _font(size, bold=False):
    paths = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
    )
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit(draw, text, max_width, start=32, minimum=15, bold=True):
    for size in range(start, minimum - 1, -1):
        font = _font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return _font(minimum, bold)


def _center(draw, box, text, font, fill):
    x1, y1, x2, y2 = box
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    x = x1 + (x2 - x1 - width) / 2
    y = y1 + (y2 - y1 - height) / 2 - bounds[1]
    draw.text((x, y), text, font=font, fill=fill)


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12,
                        watermark_id=None, settled=False):
    """Zwraca małą grafikę z dwoma prawdziwymi meczami; bez plakatu i logo."""
    if len(legs) != 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch meczów")

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

    image = Image.new("RGB", (W, H), NAVY)
    draw = ImageDraw.Draw(image)
    _center(draw, (24, 12, W-24, 66), "DUBEL • ROŻNE FT",
            _font(34, True), WHITE)

    for index, (home, away, line, odds) in enumerate(normalized):
        top = 82 + index * 164
        bottom = top + 145
        draw.rounded_rectangle((22, top, W-22, bottom), radius=18,
                               fill=PANEL, outline=BLUE, width=3)
        draw.text((46, top+13), f"MECZ {index+1}", font=_font(15, True), fill=BLUE)
        draw.text((46, top+40), home, font=_fit(draw, home, 585, 31, 17), fill=NAVY)
        draw.text((46, top+78), away, font=_fit(draw, away, 585, 31, 17), fill=NAVY)
        draw.line((665, top+18, 665, bottom-18), fill=(150, 174, 205), width=2)
        _center(draw, (680, top+22, W-38, top+58), f"OVER {line:.1f}",
                _font(19, True), MUTED)
        _center(draw, (680, top+59, W-38, bottom-14), f"{odds:.2f}",
                _font(43, True), BLUE)

    _center(draw, (24, 408, W-24, 428), "DWA MECZE  •  PEŁNY CZAS (FT)",
            _font(13, True), (177, 202, 235))
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
