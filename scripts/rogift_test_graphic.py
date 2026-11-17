#!/usr/bin/env python3
"""One-off TEST graphic for the Oko Typera FT corners Telegram channel."""
import os
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import requests

W, H = 1080, 1600
NAVY = (5, 25, 52)
BLUE = (0, 112, 210)
PAPER = (247, 244, 235)
GREEN = (151, 255, 0)
RED = (205, 24, 24)

def font(size, bold=False, italic=False):
    candidates = []
    if bold and italic:
        candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"]
    elif bold:
        candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    elif italic:
        candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"]
    candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def centered(draw, text, y, fnt, fill, width=W):
    box = draw.textbbox((0, 0), text, font=fnt)
    draw.text(((width-(box[2]-box[0]))/2, y), text, font=fnt, fill=fill)

def make_graphic():
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    # Stadium-style header
    d.rectangle((0, 0, W, 400), fill=NAVY)
    for x in range(-200, 1300, 90):
        d.line((x, 0, x+260, 400), fill=(13, 48, 83), width=3)
    centered(d, "OKO TYPERA", 35, font(54, True), (255,255,255))
    centered(d, "ROGI FT", 120, font(100, True), (255,255,255))
    centered(d, "DUBEL ROZLICZONY", 235, font(48, True), (95,180,255))
    # Test banner
    d.rounded_rectangle((55, 330, 1025, 410), radius=22, fill=(255, 215, 0))
    centered(d, "PRZYKŁAD TESTOWY • NIE GRAĆ", 347, font(36, True), NAVY)
    # selections
    rows = [
        ("Mecz A – Mecz B", "Over 5.5 rożnych FT", "1.77"),
        ("Mecz C – Mecz D", "Over 5.5 rożnych FT", "1.90"),
    ]
    y = 455
    for title, market, odds in rows:
        d.rounded_rectangle((48,y,1032,y+205), radius=28, fill=(255,255,255), outline=(210,210,210), width=3)
        d.rectangle((75,y+25,90,y+180), fill=BLUE)
        d.text((120,y+28), title, font=font(43,True), fill=(10,18,32))
        d.text((120,y+103), market, font=font(39), fill=NAVY)
        d.line((780,y+24,780,y+180), fill=BLUE, width=4)
        d.text((820,y+25), "KURS", font=font(25,True), fill=NAVY)
        d.text((815,y+73), odds, font=font(64,True), fill=BLUE)
        y += 235
    # stake and settlement math matching the example's tax illustration
    total_odds = 1.77 * 1.90
    stake = 50.00
    tax = round(stake * 0.12, 2)
    playing = stake - tax
    payout = round(playing * total_odds, 2)
    profit = round(payout - stake, 2)
    y = 950
    labels = [
        ("Stawka:", f"{stake:.2f} zł"),
        ("Podatek 12%:", f"{tax:.2f} zł"),
        ("Kwota grająca:", f"{playing:.2f} zł"),
        ("Kurs łączny:", f"{total_odds:.2f}"),
    ]
    for label, value in labels:
        d.text((105,y), label, font=font(39,True), fill=NAVY)
        box = d.textbbox((0,0),value,font=font(42,True))
        d.text((970-(box[2]-box[0]),y), value, font=font(42,True), fill=BLUE if "Kurs" in label else NAVY)
        d.line((90,y+65,990,y+65), fill=(170,170,170), width=2)
        y += 82
    d.rounded_rectangle((48,1305,1032,1435),radius=25,fill=NAVY)
    d.text((85,1330),"WYPŁATA",font=font(31,True),fill=(255,255,255))
    d.text((85,1370),f"{payout:.2f} zł",font=font(55,True),fill=(255,255,255))
    d.rounded_rectangle((550,1305,1032,1435),radius=25,fill=GREEN)
    d.text((585,1330),"ZYSK NETTO",font=font(30,True),fill=NAVY)
    d.text((585,1370),f"+{profit:.2f} zł",font=font(52,True),fill=(0,0,0))
    centered(d, "Grafika demonstracyjna — fikcyjne mecze i kursy", 1490, font(24,True), NAVY)
    return im

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    buf = BytesIO()
    make_graphic().save(buf, format="PNG", optimize=True)
    buf.seek(0)
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data={"chat_id": chat, "caption": "🧪 TEST GRAFIKI — ROGI FT\nFikcyjne mecze i kursy. Nie jest to typ do gry."},
        files={"photo": ("oko_typera_test_rogi_ft.png", buf, "image/png")},
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")
    print("TEST PHOTO SENT; message_id=" + str(payload["result"]["message_id"]))

if __name__ == "__main__":
    main()
