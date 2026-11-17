import os
import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from RogiFTDuble import publication_proof
from rogift_graphic import TEMPLATE, render_coupon_image


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

    def test_render_uses_the_exact_original_poster_canvas(self):
        self.assertTrue(TEMPLATE.is_file())
        rendered = render_coupon_image(self.legs, 3.7245, 50.0)
        with Image.open(rendered) as image:
            with Image.open(TEMPLATE) as original:
                self.assertEqual(image.size, original.size)
            self.assertEqual(image.format, "PNG")

    def test_static_branding_and_signature_area_remain_unchanged(self):
        rendered = render_coupon_image(self.legs, 3.7245, 50.0)
        with Image.open(rendered) as output, Image.open(TEMPLATE) as original:
            width, height = original.size
            top = round(height * 380 / 1536)
            bottom = round(height * 1180 / 1536)
            self.assertEqual(output.crop((0, 0, width, top)).tobytes(),
                             original.crop((0, 0, width, top)).tobytes())
            self.assertEqual(output.crop((0, bottom, width, height)).tobytes(),
                             original.crop((0, bottom, width, height)).tobytes())


if __name__ == "__main__":
    unittest.main()
