#!/usr/bin/env python3
"""Send the exact poster image uploaded to the repository."""
import os
from io import BytesIO
import time
import requests

IMAGE_URL = "https://raw.githubusercontent.com/babilonadd-art/Rogi-Do-HT-OKO-TYPERA/main/Screenshot_20261117-110724.png"

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    image_response = requests.get(IMAGE_URL, timeout=(15, 60))
    image_response.raise_for_status()
    photo_bytes = image_response.content
    if not photo_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Uploaded screenshot is not a valid PNG image")

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    caption = "🧪 TEST GRAFIKI — ROGI FT"
    last_error = None
    for attempt in range(1, 6):
        try:
            response = requests.post(
                url,
                data={"chat_id": chat, "caption": caption},
                files={"photo": ("Screenshot_20261117-110724.png", BytesIO(photo_bytes), "image/png")},
                timeout=(15, 60),
            )
            if response.status_code == 429 or response.status_code >= 500:
                last_error = RuntimeError(f"Temporary Telegram HTTP {response.status_code}: {response.text[:300]}")
                if attempt < 5:
                    time.sleep(min(2 ** attempt, 20))
                    continue
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram API error: {payload}")
            print("ORIGINAL UPLOADED POSTER SENT; message_id=" + str(payload["result"]["message_id"]))
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
