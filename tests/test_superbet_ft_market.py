import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Main


def selection(name, line=4.5, price=2.10, status=1, display=True, offer_state_id=1):
    return {
        "name": name, "price": price, "status": status, "display": display,
        "metadata": {
            "name": name, "info": name + " rzutów rożnych w pierwszej połowie",
            "special_bet_value": str(line), "offer_state_id": offer_state_id,
        },
    }


def event_payload(market_name, odds, market_status=1, inplay="NOT_STARTED",
                  offer_state_status=None):
    return {"events": [{
        "fixture": {"offer_state_status": offer_state_status or {"1": 1}},
        "inplay_stats_metadata": {"status": inplay},
        "markets": [{"name": market_name, "status": market_status, "odds": odds}],
    }]}


class SuperbetHTMarketTests(unittest.TestCase):
    @patch("Main.superbet_api")
    def test_reads_prematch_ht_over_45_price(self, api):
        api.return_value=event_payload("1. połowa - liczba rzutów rożnych",
            [selection("Powyżej 4.5",price=2.10),selection("Poniżej 4.5",price=1.80)])
        self.assertEqual(Main.superbet_ht_over_odds(123),2.10)
        api.assert_called_once_with("/v3/pl-PL/events",{"events":"123"})

    @patch("Main.superbet_api")
    def test_rejects_full_time_market(self, api):
        api.return_value=event_payload("Liczba rzutów rożnych",
            [selection("Powyżej 4.5")])
        self.assertIsNone(Main.superbet_ht_over_odds(123))

    @patch("Main.superbet_api")
    def test_rejects_team_corner_market(self, api):
        api.return_value=event_payload("1. połowa - liczba rzutów rożnych gospodarze",
            [selection("Powyżej 4.5")])
        self.assertIsNone(Main.superbet_ht_over_odds(123))

    @patch("Main.superbet_api")
    def test_rejects_wrong_line_under_and_out_of_range(self, api):
        api.return_value=event_payload("1. połowa - liczba rzutów rożnych",[
            selection("Poniżej 4.5",price=2.1),
            selection("Powyżej 3.5",line=3.5,price=2.1),
            selection("Powyżej 4.5",price=1.89),
            selection("Powyżej 4.5",price=3.51)])
        self.assertIsNone(Main.superbet_ht_over_odds(123))

    @patch("Main.superbet_api")
    def test_accepts_odds_boundaries(self, api):
        for price in (1.90,3.50):
            with self.subTest(price=price):
                api.return_value=event_payload("1. połowa - liczba rzutów rożnych",
                    [selection("Powyżej 4.5",price=price)])
                self.assertEqual(Main.superbet_ht_over_odds(123),price)

    @patch("Main.superbet_api")
    def test_rejects_inactive_hidden_or_inplay_market(self, api):
        cases=[
            event_payload("1. połowa - liczba rzutów rożnych",[selection("Powyżej 4.5",status=0)]),
            event_payload("1. połowa - liczba rzutów rożnych",[selection("Powyżej 4.5",display=False)]),
            event_payload("1. połowa - liczba rzutów rożnych",[selection("Powyżej 4.5",offer_state_id=2)]),
            event_payload("1. połowa - liczba rzutów rożnych",[selection("Powyżej 4.5")],inplay="IN_PLAY"),
            event_payload("1. połowa - liczba rzutów rożnych",[selection("Powyżej 4.5")],market_status=0)]
        for payload in cases:
            with self.subTest(payload=payload):
                api.return_value=payload
                self.assertIsNone(Main.superbet_ht_over_odds(123))

    @patch("Main.superbet_api")
    def test_returns_none_when_event_missing(self, api):
        api.return_value={"events":[]}
        self.assertIsNone(Main.superbet_ht_over_odds(123))


if __name__=="__main__":
    unittest.main()
