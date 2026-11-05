import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Main


def event_with_market(market, fixture=None, inplay=None):
    return {
        "events": [{
            "fixture": fixture or {"offer_state_status": {"1": 1}},
            "inplay_stats_metadata": {"status": inplay or "NOT_STARTED"},
            "markets": [market],
        }]
    }


def market(name, odds, status=1):
    return {"name": name, "status": status, "odds": odds}


def odd(name, line, price, status=1, display=True, offer_state_id=1):
    return {
        "name": name,
        "price": price,
        "status": status,
        "display": display,
        "metadata": {"name": name, "info": name + " rzutów rożnych w 1. połowie",
                     "special_bet_value": str(line), "offer_state_id": offer_state_id},
    }


class SuperbetHTMarketTests(unittest.TestCase):
    @patch("Main.superbet_api")
    def test_reads_real_pre_match_over_45_price(self, api):
        api.return_value = event_with_market(market(
            "1. połowa - liczba rzutów rożnych",
            [odd("powyżej 4.5", 4.5, 2.18), odd("poniżej 4.5", 4.5, 1.65)]
        ))
        self.assertEqual(Main.superbet_ht_over_odds(123), 2.18)

    @patch("Main.superbet_api")
    def test_ignores_team_corner_market(self, api):
        api.return_value = event_with_market(market(
            "1. połowa - Palmeiras liczba rzutów rożnych",
            [odd("powyżej 4.5", 4.5, 2.18)]
        ))
        self.assertIsNone(Main.superbet_ht_over_odds(123))

    @patch("Main.superbet_api")
    def test_ignores_under_and_wrong_line(self, api):
        api.return_value = event_with_market(market(
            "1. połowa - liczba rzutów rożnych",
            [odd("poniżej 4.5", 4.5, 2.18), odd("powyżej 5.5", 5.5, 2.4)]
        ))
        self.assertIsNone(Main.superbet_ht_over_odds(123))

    @patch("Main.superbet_api")
    def test_ignores_suspended_or_hidden_selection(self, api):
        api.return_value = event_with_market(market(
            "1. połowa - liczba rzutów rożnych",
            [odd("powyżej 4.5", 4.5, 2.18, status=0)]
        ))
        self.assertIsNone(Main.superbet_ht_over_odds(123))

    @patch("Main.superbet_api")
    def test_ignores_in_play_event(self, api):
        api.return_value = event_with_market(market(
            "1. połowa - liczba rzutów rożnych",
            [odd("powyżej 4.5", 4.5, 2.18)]
        ), inplay="IN_PLAY")
        self.assertIsNone(Main.superbet_ht_over_odds(123))


if __name__ == "__main__":
    unittest.main()

# Regression suite for the observed Superbet Poland HT-corners market payload.
