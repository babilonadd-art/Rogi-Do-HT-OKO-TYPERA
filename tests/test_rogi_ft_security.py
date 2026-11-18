import os
import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from RogiFTDuble import publication_proof
from rogift_graphic import W, H, render_coupon_image


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

    def test_render_uses_compact_two_match_graphic(self):
        rendered = render_coupon_image(self.legs, 3.7245, 50.0)
        with Image.open(rendered) as image:
            self.assertEqual(image.size, (W, H))
            self.assertEqual(image.size, (900, 430))
            self.assertEqual(image.format, "PNG")

    def test_settlement_flag_does_not_change_match_identity_or_layout(self):
        prematch = render_coupon_image(self.legs, 3.7245, 50.0).getvalue()
        settled = render_coupon_image(self.legs, 3.7245, 50.0, settled=True).getvalue()
        self.assertEqual(prematch, settled)

    def test_renderer_rejects_missing_real_team_names(self):
        invalid = [dict(leg) for leg in self.legs]
        invalid[0]["home"] = ""
        with self.assertRaisesRegex(ValueError, "Brak prawdziwej nazwy"):
            render_coupon_image(invalid, 3.7245, 50.0)


if __name__ == "__main__":
    unittest.main()
