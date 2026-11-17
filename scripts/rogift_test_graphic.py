#!/usr/bin/env python3
"""Send a test copy of the repository poster with the Oko Typera watermark."""
import hashlib
import os
from pathlib import Path
from io import BytesIO
import sys
import time
import requests

IMAGE_PATH = Path(__file__).resolve().parents[1] / "Screenshot_20261117-110724.png"
sys.path.insert(0, str(IMAGE_PATH.parent))
from rogift_graphic import apply_watermark

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]

    if not IMAGE_PATH.is_file():
        raise FileNotFoundError(f"Original poster not found: {IMAGE_PATH}")

    original_bytes = IMAGE_PATH.read_bytes()
    if not original_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Repository poster is not a valid PNG image")

    digest = hashlib.sha256(original_bytes).hexdigest()
    photo_bytes = apply_watermark(original_bytes)
    if not photo_bytes.startswith(b"\x89PNG\r\n\x1a\n") or photo_bytes == original_bytes:
        raise RuntimeError("Watermark was not applied to a valid PNG copy")
    print(
        f"Original kept unchanged: {IMAGE_PATH.name}; sha256={digest}. "
        "Sending separate watermarked copy."
    )

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    caption = "🧪 TEST ZNAKU WODNEGO — ROGI FT\n\n🔒 OKO TYPERA • NIE KOPIOWAĆ\nOryginalna grafika pozostaje bez zmian; do Telegrama wysyłana jest zabezpieczona kopia."
    last_error = None
    for attempt in range(1, 6):
        try:
            response = requests.post(
                url,
                data={"chat_id": chat, "caption": caption, "protect_content": True},
                files={"photo": ("OKO_TYPERA_ROGI_FT_TEST.png", BytesIO(photo_bytes), "image/png")},
                timeout=(15, 60),
            )
            if response.status_code == 429 or response.status_code >= 500:
                last_error = RuntimeError(
                    f"Temporary Telegram HTTP {response.status_code}: {response.text[:300]}"
                )
                if attempt < 5:
                    time.sleep(min(2 ** attempt, 20))
                    continue
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram API error: {payload}")
            print(
                "WATERMARKED PNG SENT; message_id="
                + str(payload["result"]["message_id"])
            )
            return
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_error = exc
            print(f"Telegram connection attempt {attempt}/5 failed: {exc}")
            if attempt < 5:
                time.sleep(min(2 ** attempt, 20))
        except requests.exceptions.HTTPError:
            raise
    raise RuntimeError(f"Telegram photo was not sent after 5 attempts: {last_error}")

if __name__ == "__main__":
    main()
