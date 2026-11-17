#!/usr/bin/env python3
"""Send one image-only layout sample using two real current FT-corners candidates."""
import os
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# Running this file from scripts/ puts that folder on sys.path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from RogiFTDuble import fetch_candidates, STAKE
from rogift_graphic import render_coupon_image

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    now = datetime.now(ZoneInfo("Europe/Warsaw"))
    candidates = fetch_candidates(now)
    if len(candidates) < 2:
        raise RuntimeError(
            f"Próbka nie została wysłana: znaleziono tylko {len(candidates)} "
            "mecz(e) spełniające kryteria. Nie używam fikcyjnych nazw drużyn."
        )
    legs = candidates[:2]
    total_odds = round(float(legs[0]["odds"]) * float(legs[1]["odds"]), 3)
    graphic = render_coupon_image(
        legs, total_odds, STAKE, coupon_no=0,
        watermark_id="TEST-UKLAD-ROGI-FT",
    )
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data={"chat_id": chat_id, "protect_content": "true"},
        files={"photo": ("probka-ukladu-rogi-ft.png", graphic, "image/png")},
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram nie przyjął próbki: {result}")
    print(
        "Wysłano próbkę grafiki bez opisu. "
        f"message_id={result['result']['message_id']}; "
        f"mecze={legs[0]['home']} - {legs[0]['away']} / "
        f"{legs[1]['home']} - {legs[1]['away']}"
    )

if __name__ == "__main__":
    main()
