"""Blue-and-white Oko Typera FT corners double poster."""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 720, 1280
ROOT = Path(__file__).resolve().parent
LOGO_SOURCE = ROOT / "Screenshot_20261117-110724.png"
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ITALIC_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"
WHITE = (248, 250, 255)
PAPER = (246, 249, 253)
NAVY = (7, 24, 53)
BLUE = (0, 112, 220)
MID_BLUE = (35, 145, 245)
MUTED = (103, 124, 151)
RED = (196, 42, 50)


def _font(size, italic=False):
    return ImageFont.truetype(ITALIC_PATH if italic else FONT_PATH, size)


def _fit(draw, text, max_width, start=34, minimum=15, italic=False):
    for size in range(start, minimum - 1, -1):
        f = _font(size, italic)
        if draw.textbbox((0, 0), text, font=f)[2] <= max_width:
            return f
    return _font(minimum, italic)


def _center(draw, box, text, font, fill):
    x1, y1, x2, y2 = box
    b = draw.textbbox((0, 0), text, font=font)
    x = x1 + (x2 - x1 - (b[2] - b[0])) / 2
    y = y1 + (y2 - y1 - (b[3] - b[1])) / 2 - b[1]
    draw.text((x, y), text, font=font, fill=fill)


def _rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _background():
    image = Image.new("RGB", (W, H))
    px = image.load()
    for y in range(H):
        t = y / H
        for x in range(W):
            glow = max(0, 1 - abs(x - W / 2) / (W / 2))
            px[x, y] = (int(4 + 5*t + 5*glow), int(15 + 12*t + 8*glow),
                        int(38 + 25*t + 14*glow))
    d = ImageDraw.Draw(image, "RGBA")
    # Subtle stadium floodlights and pitch lines, in the original blue palette.
    for cx in (42, W - 42):
        for i in range(6):
            x = cx + (i - 2.5) * 13
            d.ellipse((x-4, 42, x+4, 50), fill=(225, 241, 255, 240))
        d.ellipse((cx-75, 15, cx+75, 112), fill=(40, 130, 255, 22))
    d.line((0, 1215, W, 1215), fill=(75, 145, 220, 80), width=2)
    for x in range(-150, W + 150, 90):
        d.line((x, H, W/2 + (x-W/2)*0.25, 1215), fill=(75, 145, 220, 55), width=2)
    return image


def _draw_original_logo(image):
    # The committed screenshot is the original Oko Typera poster; reuse its crown-and-blue-eye mark.
    if not LOGO_SOURCE.exists():
        raise FileNotFoundError("Brak oryginalnego pliku logo Screenshot_20261117-110724.png")
    source = Image.open(LOGO_SOURCE).convert("RGB")
    # Crop the top-left brand lockup, not the green/neon alternative logo.
    logo = source.crop((12, 8, min(278, source.width), min(330, source.height)))
    logo.thumbnail((168, 202), Image.Resampling.LANCZOS)
    image.paste(logo, (26, 18))


def _draw_match(draw, top, leg, index):
    bottom = top + 174
    _rounded(draw, (25, top, 695, bottom), 22, PAPER, (48, 145, 235), 3)
    _rounded(draw, (36, top+10, 42, bottom-10), 3, BLUE)
    draw.text((60, top+17), f"MECZ {index}", font=_font(15), fill=BLUE)
    home, away = str(leg["home"]).strip(), str(leg["away"]).strip()
    draw.text((60, top+43), home, font=_fit(draw, home, 440, 31, 17), fill=NAVY)
    draw.text((60, top+79), away, font=_fit(draw, away, 440, 31, 17), fill=NAVY)
    _rounded(draw, (58, top+123, 420, top+158), 10, (220, 237, 255))
    _center(draw, (64, top+123, 414, top+158),
            f"OVER {float(leg['line']):.1f} ROŻNYCH FT", _font(18, True), NAVY)
    draw.line((455, top+20, 455, bottom-20), fill=(45, 132, 220), width=3)
    _center(draw, (470, top+19, 675, top+53), "KURS", _font(15), MUTED)
    _center(draw, (465, top+52, 680, top+139),
            f"{float(leg['odds']):.2f}", _font(48, True), BLUE)


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=0.12,
                        watermark_id=None, settled=False):
    """Render the two real fixtures and four requested financial fields."""
    if len(legs) != 2:
        raise ValueError("Grafika dubla wymaga dokładnie dwóch prawdziwych meczów")
    for leg in legs:
        if not str(leg.get("home") or "").strip() or not str(leg.get("away") or "").strip():
            raise ValueError("Brak prawdziwej nazwy jednej z drużyn")
        if float(leg.get("line", 0)) < 9.5:
            raise ValueError("Blokada publikacji: rożne FT poniżej OVER 9.5")
        if not 1.70 <= float(leg.get("odds", 0)) <= 3.50:
            raise ValueError("Blokada publikacji: kurs pojedynczego typu poza zakresem 1.70–3.50")

    image = _background()
    _draw_original_logo(image)
    draw = ImageDraw.Draw(image, "RGBA")

    # Original blue/white identity, with a bold sports headline beside the original logo.
    _rounded(draw, (207, 24, 694, 214), 22, (242, 247, 253), (50, 140, 235), 3)
    _center(draw, (220, 44, 682, 126), "ROGI FT", _font(62, True), NAVY)
    _center(draw, (220, 122, 682, 188), "DUBEL", _font(48, True), BLUE)
    draw.line((240, 197, 660, 197), fill=BLUE, width=4)

    _draw_match(draw, 245, legs[0], 1)
    _draw_match(draw, 435, legs[1], 2)

    tax = round(float(stake) * float(tax_rate), 2)
    played = round(float(stake) - tax, 2)
    summaries = [
        ("KURS ŁĄCZNY", f"{float(total_odds):.2f}", BLUE),
        ("STAWKA", f"{float(stake):.2f} zł", NAVY),
        ("PODATEK 12%", f"{tax:.2f} zł", RED),
        ("KWOTA GRAJĄCA", f"{played:.2f} zł", BLUE),
    ]
    positions = [(25, 635), (365, 635), (25, 765), (365, 765)]
    for (label, value, color), (x, y) in zip(summaries, positions):
        _rounded(draw, (x, y, x+330, y+112), 18, PAPER, (48, 145, 235), 2)
        draw.text((x+17, y+15), label, font=_fit(draw, label, 295, 19, 14), fill=MUTED)
        _center(draw, (x+12, y+47, x+318, y+101), value,
                _fit(draw, value, 300, 34, 20, True), color)

    _rounded(draw, (25, 905, 695, 1118), 22, (7, 24, 53), (48, 145, 235), 2)
    headline = "DUBEL ROZLICZONY" if settled else "DWA MECZE • JEDEN DUBEL"
    _center(draw, (45, 930, 675, 1005), headline, _fit(draw, headline, 600, 32, 24, True), WHITE)
    _center(draw, (45, 1010, 675, 1072), "RZUTY ROŻNE — PEŁNY CZAS (FT)", _font(21), (160, 200, 245))
    _center(draw, (35, 1150, 685, 1200), "OKO TYPERA  •  ROGI FT", _font(20, True), WHITE)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
