import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Main


def selection(name, line=9.5, price=2.10, status=1, display=True, offer_state_id=1):
    return {
        "name": name,
        "price": price,
        "status": status,
        "display": display,
        "metadata": {
            "name": name,
            "info": name + " rzutów rożnych w całym meczu",
            "special_bet_value": str(line),
            "offer_state_id": offer_state_id,
        },
    }


def event_payload(market_name, odds, market_status=1, inplay="NOT_STARTED",
                  offer_state_status=None):
    return {
        "events": [{
            "fixture": {"offer_state_status": offer_state_status or {"1": 1}},
            "inplay_stats_metadata": {"status": inplay},
            "markets": [{
                "name": market_name,
                "status": market_status,
                "odds": odds,
            }],
        }]
    }


class SuperbetFTMarketTests(unittest.TestCase):
    def test_extracts_league_from_superbet_category_metadata(self):
        event = {
            "fixture": {
                "category": {"name": "UEFA Europa League"},
                "event_name": "Home·Away",
            }
        }
        self.assertEqual(Main.extract_league_name(event), "UEFA Europa League")

    def test_extracts_league_from_nested_camel_case_field(self):
        event = {
            "fixture": {
                "competition": {"displayName": "Brazilian Serie A"}
            }
        }
        self.assertEqual(Main.extract_league_name(event), "Brazilian Serie A")

    @patch("Main.superbet_api")
    def test_returns_detailed_event_with_ft_price_for_league_lookup(self, api):
        payload = event_payload(
            "Liczba rzutów rożnych",
            [selection("Powyżej 9.5", price=2.05)],
        )
        payload["events"][0]["fixture"]["category"] = {"name": "UEFA Europa League"}
        api.return_value = payload
        result = Main.superbet_ft_event_data(123)
        self.assertEqual(result[0], 2.05)
        self.assertEqual(
            Main.extract_league_name(result[1]),
            "UEFA Europa League",
        )

    @patch("Main.superbet_api")
    def test_reads_prematch_full_time_over_95_price(self, api):
        api.return_value = event_payload(
            "Liczba rzutów rożnych",
            [selection("Powyżej 9.5", price=1.92),
             selection("Poniżej 9.5", price=1.85)],
        )
        self.assertEqual(Main.superbet_ft_over_odds(123), 1.92)
        api.assert_called_once_with("/v3/pl-PL/events", {"events": "123"})

    @patch("Main.superbet_api")
    def test_accepts_explicit_full_match_market_name(self, api):
        api.return_value = event_payload(
            "Cały mecz - liczba rzutów rożnych",
            [selection("Powyżej 9.5", price=2.25)],
        )
        self.assertEqual(Main.superbet_ft_over_odds(123), 2.25)

    @patch("Main.superbet_api")
    def test_rejects_half_time_market(self, api):
        api.return_value = event_payload(
            "1. połowa - liczba rzutów rożnych",
            [selection("Powyżej 9.5")],
        )
        self.assertIsNone(Main.superbet_ft_over_odds(123))

    @patch("Main.superbet_api")
    def test_rejects_team_corner_market(self, api):
        api.return_value = event_payload(
            "Liczba rzutów rożnych - gospodarze",
            [selection("Powyżej 9.5")],
        )
        self.assertIsNone(Main.superbet_ft_over_odds(123))

    @patch("Main.superbet_api")
    def test_rejects_under_and_wrong_line(self, api):
        api.return_value = event_payload(
            "Liczba rzutów rożnych",
            [selection("Poniżej 9.5", price=2.10),
             selection("Powyżej 8.5", line=8.5, price=2.10)],
        )
        self.assertIsNone(Main.superbet_ft_over_odds(123))

    @patch("Main.superbet_api")
    def test_rejects_odds_outside_170_to_250(self, api):
        api.return_value = event_payload(
            "Liczba rzutów rożnych",
            [selection("Powyżej 9.5", price=1.69),
             selection("Powyżej 9.5", price=2.51)],
        )
        self.assertIsNone(Main.superbet_ft_over_odds(123))

    @patch("Main.superbet_api")
    def test_accepts_odds_range_boundaries(self, api):
        for price in (1.70, 2.50):
            with self.subTest(price=price):
                api.return_value = event_payload(
                    "Liczba rzutów rożnych",
                    [selection("Powyżej 9.5", price=price)],
                )
                self.assertEqual(Main.superbet_ft_over_odds(123), price)

    @patch("Main.superbet_api")
    def test_rejects_suspended_hidden_or_inplay_market(self, api):
        cases = [
            event_payload("Liczba rzutów rożnych",
                          [selection("Powyżej 9.5", status=0)]),
            event_payload("Liczba rzutów rożnych",
                          [selection("Powyżej 9.5", display=False)]),
            event_payload("Liczba rzutów rożnych",
                          [selection("Powyżej 9.5", offer_state_id=2)]),
            event_payload("Liczba rzutów rożnych",
                          [selection("Powyżej 9.5")], inplay="IN_PLAY"),
            event_payload("Liczba rzutów rożnych",
                          [selection("Powyżej 9.5")], market_status=0),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                api.return_value = payload
                self.assertIsNone(Main.superbet_ft_over_odds(123))

    @patch("Main.superbet_api")
    def test_returns_none_when_event_missing(self, api):
        api.return_value = {"events": []}
        self.assertIsNone(Main.superbet_ft_over_odds(123))


if __name__ == "__main__":
    unittest.main()
