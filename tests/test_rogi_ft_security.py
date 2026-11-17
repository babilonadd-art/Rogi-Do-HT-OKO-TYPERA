import os
import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from RogiFTDuble import publication_proof
from rogift_graphic import (
    WATERMARK_BOTTOM,
    WATERMARK_SIDE,
    WATERMARK_TOP,
    apply_watermark,
)


class PublicationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.legs = [
            {
                "event_id": 12345,
                "league": "Liga testowa",
                "home": "Drużyna A",
                "away": "Drużyna B",
                "start_ts": 1790000000,
                "line": 9.5,
                "odds": 1.91,
            },
            {
                "event_id": 67890,
                "league": "Druga liga",
                "home": "Drużyna C",
                "away": "Drużyna D",
                "start_ts": 1790003600,
                "line": 10.5,
                "odds": 1.95,
            },
        ]

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-only-secret"}, clear=False)
    def test_signature_is_stable_and_binds_match_data(self):
        payload1, signature1 = publication_proof(self.legs, 3.7245, "coupon-123")
        payload2, signature2 = publication_proof(self.legs, 3.7245, "coupon-123")
        changed = [dict(leg) for leg in self.legs]
        changed[0]["home"] = "Podmieniona drużyna"
        _, changed_signature = publication_proof(changed, 3.7245, "coupon-123")

        self.assertEqual(payload1, payload2)
        self.assertEqual(signature1, signature2)
        self.assertEqual(len(signature1), 24)
        self.assertNotEqual(signature1, changed_signature)
        self.assertEqual(payload1["legs"][0]["market"], "OVER_CORNERS_FT")

    def test_qr_frame_does_not_change_or_cover_the_original_pick(self):
        original = Image.new("RGBA", (360, 240), (31, 62, 93, 255))
        for x in range(360):
            for y in range(240):
                original.putpixel((x, y), (x % 256, y % 256, (x + y) % 256, 255))
        source = BytesIO()
        original.save(source, format="PNG")

        framed = apply_watermark(
            source.getvalue(),
            watermark_id="OT-TEST-123456",
            signature="ABCDEF0123456789ABCDEF01",
            verification_url="https://t.me/official_channel/123",
        )
        with Image.open(BytesIO(framed)) as image:
            self.assertEqual(
                image.size,
                (360 + 2 * WATERMARK_SIDE, 240 + WATERMARK_TOP + WATERMARK_BOTTOM),
            )
            inside = image.crop((
                WATERMARK_SIDE,
                WATERMARK_TOP,
                WATERMARK_SIDE + 360,
                WATERMARK_TOP + 240,
            ))
            self.assertEqual(inside.tobytes(), original.tobytes())


if __name__ == "__main__":
    unittest.main()
