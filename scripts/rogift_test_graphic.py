#!/usr/bin/env python3
"""Send the exact PNG poster stored in the repository; never generate or alter it."""
import hashlib
import os
from pathlib import Path
from io import BytesIO
import time
import requests

IMAGE_PATH = Path(__file__).resolve().parents[1] / "Screenshot_20261117-110724.png"

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]

    if not IMAGE_PATH.is_file():
        raise FileNotFoundError(f"Original poster not found: {IMAGE_PATH}")

    photo_bytes = IMAGE_PATH.read_bytes()
    if not photo_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Repository poster is not a valid PNG image")

    digest = hashlib.sha256(photo_bytes).hexdigest()
    print(f"Sending repository original unchanged: {IMAGE_PATH.name}; sha256={digest}")

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    caption = "🧪 TEST GRAFIKI — ROGI FT"
    last_error = None
    for attempt in range(1, 6):
        try:
            response = requests.post(
                url,
                data={"chat_id": chat, "caption": caption, "protect_content": True},
                files={"photo": (IMAGE_PATH.name, BytesIO(photo_bytes), "image/png")},
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
                "ORIGINAL PNG SENT UNMODIFIED; message_id="
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
