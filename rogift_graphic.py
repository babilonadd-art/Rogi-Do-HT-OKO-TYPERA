"""Render FT-corners doubles on a freshly drawn Oko Typera poster.

No screenshot/template containing old text is loaded or reused. Every pixel,
including the background and all text panels, is generated for each coupon.
"""
from io import BytesIO
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont
import math

W, H = 1024, 1536
NAVY = (5, 19, 43)
NAVY2 = (10, 43, 91)
BLUE = (0, 126, 232)
PALE = (244, 248, 253)
WHITE = (255, 255, 255)
GREEN = (143, 255, 0)
DARK = (5, 22, 47)
RED = (195, 36, 45)
LOCAL_TZ = ZoneInfo("Europe/Warsaw")


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


def _fit(draw, text, max_width, start=40, minimum=16):
    for size in range(start, minimum - 1, -1):
        font = _font(size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    raise ValueError(f"Tekst nie mieści się na grafice: {text!r}")


def _center(draw, box, text, font, fill):
    x1, y1, x2, y2 = box
    b = draw.textbbox((0, 0), text, font=font)
    tw, th = b[2] - b[0], b[3] - b[1]
    if tw > x2 - x1 or th > y2 - y1:
        raise ValueError(f"Tekst wychodzi poza pole grafiki: {text!r}")
    draw.text((round(x1 + (x2-x1-tw)/2), round(y1 + (y2-y1-th)/2-b[1])),
              text, font=font, fill=fill)


def _money(value):
    return f"{float(value):.2f}".replace(".", ",") + " zł"


def _ball(draw, cx, cy, radius):
    draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius),
                 fill=(247, 249, 252), outline=BLUE, width=4)
    draw.ellipse((cx-radius*.24, cy-radius*.24, cx+radius*.24, cy+radius*.24),
                 fill=NAVY)
    for angle in (0, 72, 144, 216, 288):
        a = math.radians(angle)
        x, y = cx + math.cos(a)*radius*.62, cy + math.sin(a)*radius*.62
        draw.polygon([(x, y-radius*.16), (x+radius*.15, y-radius*.05),
                      (x+radius*.09, y+radius*.14), (x-radius*.09, y+radius*.14),
                      (x-radius*.15, y-radius*.05)], fill=NAVY)


def _fresh_canvas():
    image = Image.new("RGB", (W, H), (3, 8, 17))
    px = image.load()
    for y in range(H):
        t=y/(H-1); glow=max(0.0,1.0-abs(t-.55)*2)
        base=(3+int(4*glow),8+int(20*glow),17+int(28*glow))
        for x in range(W):
            side=max(0.0,1.0-abs(x-W/2)/(W/2))
            px[x,y]=(base[0],base[1]+int(10*side*glow),base[2]+int(15*side*glow))
    d=ImageDraw.Draw(image)
    for x in range(-H,W,105): d.line((x,0,x+H,H),fill=(7,35,30),width=3)
    d.rounded_rectangle((34,30,990,250),radius=38,fill=(2,9,15),outline=GREEN,width=6)
    d.polygon([(76,135),(105,60),(145,110),(181,48),(218,110),(257,60),(284,135)],fill=GREEN,outline=WHITE)
    d.rectangle((86,139,273,158),fill=GREEN)
    d.arc((110,150,250,228),190,350,fill=WHITE,width=6); d.arc((110,150,250,228),10,170,fill=WHITE,width=6)
    d.ellipse((172,174,194,196),fill=RED)
    d.text((315,62),"ROGI DO HT",font=_font(54),fill=WHITE)
    d.text((315,124),"OKO TYPERA",font=_font(66),fill=GREEN)
    d.rounded_rectangle((315,195,720,232),radius=14,fill=GREEN)
    _center(d,(325,195,710,230),"DUBEL ROGI FT",_font(27),(0,0,0)); _ball(d,866,140,68)
    d.rounded_rectangle((55,278,969,360),radius=24,fill=GREEN,outline=(220,255,180),width=3)
    _center(d,(75,286,949,350),"DWA MECZE  •  OVER RZUTÓW ROŻNYCH",_fit(d,"DWA MECZE  •  OVER RZUTÓW ROŻNYCH",840,35,22),(0,0,0))
    for y1,y2 in ((390,570),(590,770)):
        d.rounded_rectangle((48,y1,976,y2),radius=30,fill=(3,12,19),outline=GREEN,width=5)
        d.rounded_rectangle((66,y1+18,78,y2-18),radius=5,fill=GREEN); _ball(d,145,(y1+y2)//2,34)
        d.line((744,y1+20,744,y2-20),fill=GREEN,width=3)
    d.rounded_rectangle((48,795,976,1100),radius=30,fill=(3,12,19),outline=GREEN,width=5)
    for y in (870,945,1020): d.line((90,y,934,y),fill=(47,75,66),width=2)
    d.rounded_rectangle((48,1120,490,1305),radius=28,fill=(3,12,19),outline=GREEN,width=4)
    d.rounded_rectangle((510,1120,976,1305),radius=28,fill=GREEN,outline=(210,255,170),width=4)
    d.rounded_rectangle((48,1325,976,1495),radius=26,fill=(2,18,23),outline=(25,190,100),width=3)
    d.line((512,1350,512,1470),fill=(25,190,100),width=3)
    _center(d,(70,1435,954,1485),"LINKI DO RYNKU ROŻNYCH FT POD GRAFIKĄ",_font(22),(195,235,215))
    return image


def render_coupon_image(legs,total_odds,stake,coupon_no=1,tax_rate=0.12,watermark_id=None,settled=False):
    if len(legs)!=2: raise ValueError("Grafika dubla wymaga dokładnie dwóch meczów")
    normalized=[]
    for leg in legs:
        home,away=str(leg.get("home") or "").strip(),str(leg.get("away") or "").strip()
        if not home or not away or home.lower() in {"a","b","mecz a","mecz b","team a","team b"} or away.lower() in {"a","b","mecz a","mecz b","team a","team b"}: raise ValueError("Brak prawdziwej nazwy jednej z drużyn")
        line,odds=float(leg.get("line",0)),float(leg.get("odds",0))
        if not settled and not 8.5<=line<=9.5: raise ValueError("Dozwolone wyłącznie OVER 8.5–9.5 rożnych FT")
        if not settled and not 1.60<=odds<=2.20: raise ValueError("Kurs poza zakresem 1.60–2.20")
        if leg.get("start_ts") is None: raise ValueError("Brak godziny rozpoczęcia meczu")
        kickoff=datetime.fromtimestamp(int(leg["start_ts"]),timezone.utc).astimezone(LOCAL_TZ)
        normalized.append((home,away,line,odds,kickoff.strftime("%H:%M")))
    stake,total_odds,tax_rate=float(stake),float(total_odds),float(tax_rate)
    if stake<=0 or total_odds<=0 or not 0<=tax_rate<1: raise ValueError("Nieprawidłowa stawka, kurs lub podatek")
    if abs(normalized[0][3]*normalized[1][3]-total_odds)>0.02: raise ValueError("Kurs łączny nie zgadza się z kursami obu meczów")
    image=_fresh_canvas(); d=ImageDraw.Draw(image)
    for (home,away,line,odds,kickoff),(top,bottom) in zip(normalized,((390,570),(590,770))):
        fixture=f"{home}  –  {away}"
        _center(d,(180,top+14,725,top+78),fixture,_fit(d,fixture,525,34,17),WHITE)
        d.rounded_rectangle((180,top+83,725,top+126),radius=12,fill=(13,40,35))
        label=f"START MECZU: {kickoff}"; _center(d,(190,top+84,715,top+125),label,_fit(d,label,500,27,19),WHITE)
        market=f"OVER {line:.1f} ROŻNYCH FT"; _center(d,(180,top+132,725,top+174),market,_fit(d,market,525,28,19),GREEN)
        _center(d,(752,top+20,955,top+70),"KURS",_font(24),WHITE)
        d.rounded_rectangle((760,top+78,955,top+150),radius=17,fill=GREEN)
        _center(d,(765,top+80,950,top+148),f"{odds:.2f}",_font(54),(0,0,0))
    tax=round(stake*tax_rate,2); playable=round(stake-tax,2); payout=round(playable*total_odds,2); profit=round(payout-stake,2)
    rows=(("KURS ŁĄCZNY",f"{total_odds:.2f}",GREEN),("STAWKA",_money(stake),(255,45,55)),(f"PODATEK {tax_rate*100:.0f}%",_money(tax),(255,95,95)),("KWOTA GRAJĄCA",_money(playable),WHITE))
    for i,(label,value,color) in enumerate(rows):
        y=805+i*74; _center(d,(105,y,560,y+65),label,_font(29),WHITE)
        _center(d,(580,y,920,y+65),value,_fit(d,value,320,39,24),color)
    _center(d,(72,1135,466,1190),"POTENCJALNA WYPŁATA",_font(28),WHITE)
    _center(d,(72,1200,466,1280),_money(payout),_fit(d,_money(payout),360,45,25),(255,220,40))
    _center(d,(530,1135,956,1190),"POTENCJALNY ZYSK",_font(28),(0,0,0))
    profit_text=("+" if profit>=0 else "")+_money(profit)
    _center(d,(530,1200,956,1280),profit_text,_fit(d,profit_text,390,45,25),(0,0,0))
    if settled:
        d.rounded_rectangle((300,20,724,72),radius=18,fill=RED)
        _center(d,(310,22,714,70),"KUPON ROZLICZONY",_font(27),WHITE)
    output=BytesIO(); image.save(output,format="PNG",optimize=True); output.seek(0); return output
