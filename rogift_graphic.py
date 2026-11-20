"""Render FT doubles over the user's updated Oko Typera poster."""
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

W, H = 1024, 1024
TZ=ZoneInfo("Europe/Warsaw")
TEMPLATE=Path(__file__).resolve().parent/"Screenshot_20261120-092141~2.jpg"
WHITE=(255,255,255); GREEN=(143,255,0); RED=(255,45,55); DARK=(3,12,17)

def font(n):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf","/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"):
        try: return ImageFont.truetype(p,n)
        except OSError: pass
    return ImageFont.load_default()

def fit(d,s,w,hi=28,lo=10):
    for n in range(hi,lo-1,-1):
        f=font(n)
        if d.textbbox((0,0),s,font=f)[2]<=w: return f
    return font(lo)

def center(d,b,s,f,c):
    x1,y1,x2,y2=b; z=d.textbbox((0,0),s,font=f); tw=z[2]-z[0]; th=z[3]-z[1]
    d.text((x1+(x2-x1-tw)/2,y1+(y2-y1-th)/2-z[1]),s,font=f,fill=c)

def money(v): return f"{float(v):.2f}".replace(".",",")+" zł"

def original_artwork():
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"Brak nowej grafiki użytkownika: {TEMPLATE}")
    with Image.open(TEMPLATE) as source:
        im = source.convert("RGB")
    if im.width != im.height:
        side = min(im.width, im.height)
        left = (im.width - side) // 2
        top = (im.height - side) // 2
        im = im.crop((left, top, left + side, top + side))
    return im.resize((W, H), Image.Resampling.LANCZOS)

def render_coupon_image(legs,total_odds,stake,coupon_no=1,tax_rate=.12,watermark_id=None,settled=False,night=False):
    if len(legs)!=2: raise ValueError("Grafika dubla wymaga dokładnie dwóch meczów")
    rows=[]
    for leg in legs:
        home=str(leg.get("home") or "").strip(); away=str(leg.get("away") or "").strip()
        league=str(leg.get("league") or "").strip()
        if not home or not away or not league or home.lower() in {"a","b","mecz a","mecz b","team a","team b"} or away.lower() in {"a","b","mecz a","mecz b","team a","team b"}:
            raise ValueError("Brak prawdziwej nazwy drużyny lub ligi")
        line=float(leg.get("line",0)); odds=float(leg.get("odds",0))
        if not settled and not 8.5<=line<=9.5: raise ValueError("Dozwolone wyłącznie OVER 8.5–9.5 rożnych FT")
        if not settled and not 1.60<=odds<=2.20: raise ValueError("Kurs poza zakresem 1.60–2.20")
        if leg.get("start_ts") is None: raise ValueError("Brak godziny rozpoczęcia meczu")
        kickoff=datetime.fromtimestamp(int(leg["start_ts"]),timezone.utc).astimezone(TZ).strftime("%H:%M")
        rows.append((home,away,league,line,odds,kickoff))
    stake=float(stake); total_odds=float(total_odds); tax_rate=float(tax_rate)
    if stake<=0 or total_odds<=0 or not 0<=tax_rate<1: raise ValueError("Nieprawidłowa stawka, kurs lub podatek")
    if abs(rows[0][4]*rows[1][4]-total_odds)>.02: raise ValueError("Kurs łączny nie zgadza się z kursami obu meczów")
    im=original_artwork(); d=ImageDraw.Draw(im)
    if night:
        d.rounded_rectangle((300,330,724,382),radius=16,fill=RED)
        center(d,(310,332,714,380),"NOCNY DUBEL",font(34),WHITE)
    for i,(home,away,league,line,odds,kickoff) in enumerate(rows):
        y=397+i*128
        d.rounded_rectangle((178,y,800,y+120),radius=18,fill=DARK)
        center(d,(188,y+3,550,y+40),home,fit(d,home,350,32,19),WHITE)
        center(d,(188,y+37,550,y+74),away,fit(d,away,350,32,19),WHITE)
        center(d,(188,y+76,550,y+99),league.upper(),fit(d,league.upper(),350,22,13),WHITE)
        center(d,(188,y+97,550,y+119),kickoff,fit(d,kickoff,350,29,25),RED)
        center(d,(555,y+15,790,y+58),f"OVER {line:.1f}",font(28),GREEN)
        center(d,(555,y+57,790,y+95),"ROGÓW FT",font(22),WHITE)
        d.rounded_rectangle((810,y+24,990,y+98),radius=18,fill=GREEN)
        center(d,(814,y+25,986,y+97),f"{odds:.2f}",fit(d,f"{odds:.2f}",160,58,34),(0,0,0))
    combined_value = rows[0][4] * rows[1][4]
    combined = f"{combined_value:.2f}"
    potential_payout = money(stake * combined_value)
    d.rectangle((440,735,580,790),fill=(1,2,2))
    center(d,(440,735,580,790),combined,fit(d,combined,132,64,48),GREEN)
    d.rectangle((720,735,990,790),fill=(1,2,2))
    center(d,(720,735,990,790),potential_payout,fit(d,potential_payout,260,42,26),GREEN)
    out=BytesIO(); im.save(out,format="PNG",optimize=True); out.seek(0); return out
