#!/usr/bin/env python3
"""Send a test copy of the repository poster with the Oko Typera watermark."""
import hashlib
import hmac
import json
import os
from pathlib import Path
from io import BytesIO
import sys
import time
import requests

IMAGE_PATH = Path(__file__).resolve().parents[1] / "Screenshot_20261117-110724.png"
sys.path.insert(0, str(IMAGE_PATH.parent))
from rogift_graphic import apply_watermark, WATERMARK_SIDE, WATERMARK_TOP, WATERMARK_BOTTOM
from PIL import Image

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]

    if not IMAGE_PATH.is_file():
        raise FileNotFoundError(f"Original poster not found: {IMAGE_PATH}")

    original_bytes = IMAGE_PATH.read_bytes()
    if not original_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Repository poster is not a valid PNG image")

    digest = hashlib.sha256(original_bytes).hexdigest()
    publication_code = "OT-TEST-20261117"
    proof_payload = {
        "brand": "OKO TYPERA",
        "test_id": publication_code,
        "source_sha256": digest,
        "purpose": "QR_POST_VERIFICATION_TEST",
    }
    canonical = json.dumps(proof_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signing_secret = os.getenv("PUBLICATION_SIGNING_SECRET") or token
    signature = hmac.new(
        signing_secret.encode("utf-8"),
        b"OKO-TYPERA-ROGI-FT-TEST-V1\\0" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24].upper()
    photo_bytes = apply_watermark(
        original_bytes, watermark_id=publication_code, signature=signature
    )
    # Verify that the source graphic is preserved pixel-for-pixel inside the frame.
    with Image.open(BytesIO(original_bytes)) as original_image:
        original_rgba = original_image.convert("RGBA")
    with Image.open(BytesIO(photo_bytes)) as marked_image:
        expected_size = (
            original_rgba.width + 2 * WATERMARK_SIDE,
            original_rgba.height + WATERMARK_TOP + WATERMARK_BOTTOM,
        )
        if marked_image.size != expected_size:
            raise RuntimeError(f"Unexpected framed image size: {marked_image.size}")
        preserved = marked_image.crop((
            WATERMARK_SIDE,
            WATERMARK_TOP,
            WATERMARK_SIDE + original_rgba.width,
            WATERMARK_TOP + original_rgba.height,
        ))
        if preserved.tobytes() != original_rgba.tobytes():
            raise RuntimeError("Watermark overlaps or changes the original pick image")
    if not photo_bytes.startswith(b"\x89PNG\r\n\x1a\n") or photo_bytes == original_bytes:
        raise RuntimeError("Watermark was not applied to a valid PNG copy")
    print(
        f"Original kept unchanged: {IMAGE_PATH.name}; sha256={digest}. "
        "Sending separate watermarked copy."
    )

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    caption = f"🧪 TEST PODPISU CYFROWEGO — ROGI FT\n\n🔐 ID: {publication_code}\n🛡 PODPIS: {signature}\nQR prowadzi do dokładnie tego posta w oficjalnym kanale."
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
            message_id = int(payload["result"]["message_id"])
            chat_response = requests.post(
                f"https://api.telegram.org/bot{token}/getChat",
                data={"chat_id": chat},
                timeout=20,
            )
            chat_response.raise_for_status()
            chat_payload = chat_response.json()
            if not chat_payload.get("ok"):
                raise RuntimeError(f"Telegram getChat error: {chat_payload}")
            chat_info = chat_payload.get("result") or {}
            if chat_info.get("username"):
                verification_url = f"https://t.me/{chat_info['username']}/{message_id}"
            elif str(chat).startswith("-100"):
                verification_url = f"https://t.me/c/{str(chat)[4:]}/{message_id}"
            else:
                raise RuntimeError("Nie udało się utworzyć linku weryfikacyjnego do kanału")

            final_bytes = apply_watermark(
                original_bytes,
                watermark_id=publication_code,
                signature=signature,
                verification_url=verification_url,
            )
            final_caption = caption + f"\n🔎 SPRAWDŹ ORYGINAŁ: {verification_url}"
            media = {
                "type": "photo",
                "media": "attach://photo",
                "caption": final_caption,
            }
            edit_response = requests.post(
                f"https://api.telegram.org/bot{token}/editMessageMedia",
                data={
                    "chat_id": chat,
                    "message_id": message_id,
                    "media": json.dumps(media, ensure_ascii=False),
                },
                files={
                    "photo": (
                        f"{publication_code}_SIGNED_QR_TEST.png",
                        BytesIO(final_bytes),
                        "image/png",
                    )
                },
                timeout=45,
            )
            edit_response.raise_for_status()
            edit_payload = edit_response.json()
            if not edit_payload.get("ok"):
                raise RuntimeError(f"Telegram editMessageMedia error: {edit_payload}")
            print(
                "SIGNED QR GRAPHIC SENT; message_id="
                + str(message_id)
                + "; verification_url="
                + verification_url
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
