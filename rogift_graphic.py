"""Clean FT doubles list. The legacy poster is used only to crop the original bull."""
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

TZ = ZoneInfo("Europe/Warsaw")
W, H = 1080, 1420
SOURCE_ART = Path(__file__).resolve().parent / "rogi_ft_template_new.png"
BG, PANEL = (5, 15, 10), (9, 29, 17)
GREEN, LIME = (133, 255, 0), (190, 255, 0)
WHITE, MUTED = (247, 250, 246), (190, 207, 193)


def font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def fit(draw, value, max_width, high=34, low=16):
    for size in range(high, low - 1, -1):
        selected = font(size)
        if draw.textbbox((0, 0), str(value), font=selected)[2] <= max_width:
            return selected
    return font(low)


def center(draw, bounds, value, selected_font, color, **options):
    x1, y1, x2, y2 = bounds
    box = draw.textbbox((0, 0), str(value), font=selected_font)
    width, height = box[2] - box[0], box[3] - box[1]
    draw.text((x1 + (x2-x1-width)/2, y1 + (y2-y1-height)/2 - box[1]),
              str(value), font=selected_font, fill=color, **options)


def calculate_coupon_totals(leg_odds, stake):
    if len(leg_odds) != 2:
        raise ValueError("Dubel musi zawierać dokładnie dwa kursy")
    combined = (Decimal(str(leg_odds[0])) * Decimal(str(leg_odds[1]))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    payout = (Decimal(str(stake)) * combined).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    return combined, payout


def _bull_artwork():
    if not SOURCE_ART.is_file():
        raise FileNotFoundError(f"Brak źródłowej grafiki z oryginalnym bykiem: {SOURCE_ART}")
    with Image.open(SOURCE_ART) as source:
        source = source.convert("RGB")
        if source.size != (720, 751):
            raise ValueError(f"Nieprawidłowy rozmiar grafiki źródłowej: {source.size}; oczekiwano 720x751")
        # Wyłącznie byk, korona i zielone tło; bez starego napisu i pól kuponu.
        return source.crop((213, 0, 489, 171)).copy()


def render_coupon_image(legs, total_odds, stake, coupon_no=1, tax_rate=.12,
                        watermark_id=None, settled=False, night=False):
    if len(legs) != 2:
        raise ValueError("Dubel musi zawierać dokładnie dwa mecze")
    rows = []
    for leg in legs:
        home, away = str(leg.get("home") or "").strip(), str(leg.get("away") or "").strip()
        league = str(leg.get("league") or "").strip()
        if not home or not away or not league or home.lower() in {"a","b","mecz a","mecz b","team a"} or away.lower() in {"a","b","mecz a","mecz b","team b"}:
            raise ValueError("Brak prawdziwej nazwy drużyny lub ligi")
        line, odds = float(leg.get("line", 0)), float(leg.get("odds", 0))
        if not settled and not 8.5 <= line <= 9.5:
            raise ValueError("Nieprawidłowa linia rożnych FT")
        if not settled and odds <= 1.0:
            raise ValueError("Nieprawidłowy kurs meczu")
        if leg.get("start_ts") is None:
            raise ValueError("Brak daty i godziny rozpoczęcia meczu")
        kickoff = datetime.fromtimestamp(int(leg["start_ts"]), timezone.utc).astimezone(TZ)
        rows.append((home, away, league, line, odds, kickoff))
    stake, total_odds = float(stake), float(total_odds)
    if stake <= 0 or total_odds <= 0:
        raise ValueError("Nieprawidłowa stawka lub kurs")
    if abs(rows[0][4] * rows[1][4] - total_odds) > .02:
        raise ValueError("Kurs łączny nie zgadza się z kursami obu meczów")

    im = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle((12, 12, W-12, H-12), radius=38, outline=GREEN, width=5)
    bull = _bull_artwork()
    bull.thumbnail((390, 242), Image.Resampling.LANCZOS)
    im.paste(bull, ((W-bull.width)//2, 24))
    draw = ImageDraw.Draw(im)
    center(draw, (40, 245, W-40, 325), "ROGI FT DUBLE", font(64), GREEN,
           stroke_width=2, stroke_fill=(0,0,0))
    if night:
        center(draw, (40, 325, W-40, 378), "NOCNE TYPY", font(30), WHITE)

    for index, (home, away, league, line, odds, kickoff) in enumerate(rows, 1):
        y = 390 + (index-1)*440
        draw.rounded_rectangle((38,y,W-38,y+390), radius=30, fill=PANEL, outline=GREEN, width=4)
        draw.rounded_rectangle((58,y+18,W-58,y+78), radius=18,
                               fill=(24,57,28), outline=GREEN, width=2)
        center(draw, (70,y+18,W-70,y+78), f"MECZ {index}", font(34), LIME)
        fixture = f"{home} – {away}"
        center(draw, (70,y+95,W-70,y+158), fixture, fit(draw,fixture,920,43,24), WHITE)
        center(draw, (70,y+166,W-70,y+213), league, fit(draw,league,900,29,18), MUTED)
        date_line = kickoff.strftime("%d.%m.%Y • %H:%M")
        center(draw, (70,y+218,W-70,y+266), f"ROZPOCZĘCIE: {date_line}", font(27), WHITE)
        draw.rounded_rectangle((70,y+286,W-70,y+366), radius=18,
                               fill=(21,43,19), outline=GREEN, width=3)
        draw.text((98,y+306), f"OVER {line:.1f} ROGÓW FT", font=font(31), fill=GREEN)
        odds_text = f"{odds:.2f}"
        odds_box = draw.textbbox((0,0), odds_text, font=font(43))
        draw.text((W-98-(odds_box[2]-odds_box[0]),y+298), odds_text, font=font(43), fill=LIME)

    combined, payout = calculate_coupon_totals([rows[0][4],rows[1][4]], stake)
    draw.rounded_rectangle((90,1260,W-90,1365), radius=30, fill=(0,0,0), outline=GREEN, width=4)
    center(draw,(105,1268,W-105,1310),f"KURS ŁĄCZNY: {combined:.2f}",font(37),GREEN)
    footer=f"STAWKA {stake:.0f} PLN  •  MOŻLIWA WYGRANA {payout:.2f} PLN"
    center(draw,(105,1310,W-105,1355),footer,fit(draw,footer,900,24,17),WHITE)
    output=BytesIO()
    im.save(output,format="PNG",optimize=True)
    output.seek(0)
    return output
