"""Render FT doubles over the original Oko Typera poster saved in the repository."""
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

TZ=ZoneInfo("Europe/Warsaw")
TEMPLATE=Path(__file__).resolve().parent/"Screenshot_20261117-110724.png"
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
    if not TEMPLATE.is_file(): raise FileNotFoundError(f"Brak oryginalnej grafiki: {TEMPLATE}")
    im=Image.open(TEMPLATE).convert("RGB")
    # The saved source is a phone screenshot; isolate its square poster.
    if im.height>im.width*1.15:
        side=im.width; top=max(0,min(im.height-side,int(im.height*.456-side/2)))
        im=im.crop((0,top,side,top+side))
    elif im.width>im.height*1.15:
        side=im.height; left=(im.width-side)//2; im=im.crop((left,0,left+side,side))
    return im.resize((1024,1024),Image.Resampling.LANCZOS)

def render_coupon_image(legs,total_odds,stake,coupon_no=1,tax_rate=.12,watermark_id=None,settled=False):
    if len(legs)!=2: raise ValueError("Grafika dubla wymaga dokładnie dwóch meczów")
    rows=[]
    for leg in legs:
        home=str(leg.get("home") or "").strip(); away=str(leg.get("away") or "").strip()
        league=str(leg.get("league") or "").strip()
        if not home or not away or not league or home.lower() in {"a","b","mecz a","mecz b","team a","team b"} or away.lower() in {"a","b","mecz a","mecz b","team a","team b"}:
            raise ValueError("Brak prawdziwych nazw drużyn lub ligi")
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
    # Preserve the user's bull, stadium, neon borders, balls and overall layout.
    for i,(home,away,league,line,odds,kickoff) in enumerate(rows):
        y=397+i*128
        d.rounded_rectangle((178,y,800,y+120),radius=18,fill=DARK)
        fixture=f"{home} – {away}"
        center(d,(188,y+7,550,y+48),fixture,fit(d,fixture,350,29,15),WHITE)
        league_time=f"{league}  •  {kickoff}"
        center(d,(188,y+48,550,y+82),league_time,fit(d,league_time,350,20,11),WHITE)
        center(d,(555,y+15,790,y+58),f"OVER {line:.1f}",font(28),GREEN)
        center(d,(555,y+57,790,y+95),"ROGÓW FT",font(22),WHITE)
        d.rounded_rectangle((810,y+24,990,y+98),radius=18,fill=GREEN)
        center(d,(814,y+25,986,y+97),f"{odds:.2f}",fit(d,f"{odds:.2f}",160,58,34),(0,0,0))
    tax=round(stake*tax_rate,2); playable=round(stake-tax,2)
    payout=round(playable*total_odds,2); profit=round(payout-stake,2)
    # Update only the numeric areas of the original stake / odds / payout panel.
    for box in ((245,690,410,795),(610,690,770,795),(790,690,1010,760),(690,760,1010,825)):
        d.rounded_rectangle(box,radius=12,fill=DARK)
    center(d,(245,690,410,795),money(stake),fit(d,money(stake),155,42,24),RED)
    center(d,(610,690,770,795),f"{total_odds:.2f}",fit(d,f"{total_odds:.2f}",150,52,28),GREEN)
    center(d,(790,690,1010,760),money(payout),fit(d,money(payout),210,34,18),(255,220,40))
    p=("+" if profit>=0 else "")+money(profit)
    center(d,(690,760,1010,825),p,fit(d,p,310,33,18),GREEN)
    out=BytesIO(); im.save(out,format="PNG",optimize=True); out.seek(0); return out
