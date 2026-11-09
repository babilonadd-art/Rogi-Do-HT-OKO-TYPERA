import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Main


def selection(name, line=8.5, price=2.10, status=1, display=True, offer_state_id=1):
    return {
        "name": name, "price": price, "status": status, "display": display,
        "metadata": {
            "name": name, "info": name + " rzutów rożnych",
            "special_bet_value": str(line), "offer_state_id": offer_state_id,
        },
    }


def payload(market_name, odds, market_status=1, inplay="NOT_STARTED",
            offer_state_status=None):
    return {"events": [{
        "id": 123,
        "fixture": {"offer_state_status": offer_state_status or {"1": 1}},
        "inplay_stats_metadata": {"status": inplay},
        "markets": [{"name": market_name, "status": market_status, "odds": odds}],
    }]}


class SuperbetFTCornersTests(unittest.TestCase):
    @patch("Main.superbet_api")
    def test_reads_exact_full_time_over_85(self, api):
        api.return_value=payload("Liczba rzutów rożnych",
            [selection("Powyżej 8.5"),selection("Poniżej 8.5",price=1.80)])
        self.assertEqual(Main.superbet_ft_corners_over_odds(123),2.10)

    @patch("Main.superbet_api")
    def test_rejects_half_time_and_team_markets(self, api):
        for market in ("1. połowa - liczba rzutów rożnych",
                       "Liczba rzutów rożnych gospodarze"):
            with self.subTest(market=market):
                api.return_value=payload(market,[selection("Powyżej 8.5")])
                self.assertIsNone(Main.superbet_ft_corners_over_odds(123))

    @patch("Main.superbet_api")
    def test_rejects_wrong_line_under_and_out_of_range(self, api):
        api.return_value=payload("Liczba rzutów rożnych",[
            selection("Poniżej 8.5"),
            selection("Powyżej 9.5",line=9.5),
            selection("Powyżej 8.5",price=1.69),
            selection("Powyżej 8.5",price=2.61)])
        self.assertIsNone(Main.superbet_ft_corners_over_odds(123))

    @patch("Main.superbet_api")
    def test_rejects_inactive_hidden_or_live_market(self, api):
        cases=[
            payload("Liczba rzutów rożnych",[selection("Powyżej 8.5",status=0)]),
            payload("Liczba rzutów rożnych",[selection("Powyżej 8.5",display=False)]),
            payload("Liczba rzutów rożnych",[selection("Powyżej 8.5",offer_state_id=2)]),
            payload("Liczba rzutów rożnych",[selection("Powyżej 8.5")],inplay="IN_PLAY"),
            payload("Liczba rzutów rożnych",[selection("Powyżej 8.5")],market_status=0)]
        for data in cases:
            with self.subTest(data=data):
                api.return_value=data
                self.assertIsNone(Main.superbet_ft_corners_over_odds(123))

    @patch("Main.superbet_api")
    def test_accepts_single_detail_event_without_id(self, api):
        data=payload("Liczba rzutów rożnych",[selection("Powyżej 8.5")])
        data["events"][0].pop("id")
        api.return_value=data
        self.assertEqual(Main.superbet_ft_corners_over_odds(123),2.10)

    @patch("Main.superbet_api")
    def test_rejects_wrong_event_id(self, api):
        api.return_value=payload("Liczba rzutów rożnych",[selection("Powyżej 8.5")])
        api.return_value["events"][0]["id"]=456
        self.assertIsNone(Main.superbet_ft_corners_over_odds(123))


    @patch("Main.superbet_api")
    def test_prefers_explicit_corners_market_deep_link(self, api):
        data=payload("Liczba rzutów rożnych",[selection("Powyżej 8.5")])
        data["events"][0]["markets"][0]["url"]="https://superbet.pl/zaklady-bukmacherskie/pilka-nozna/test#corners"
        api.return_value=data
        self.assertEqual(Main.superbet_match_url(123,"Home","Away",0),
                         "https://superbet.pl/zaklady-bukmacherskie/pilka-nozna/test#corners")

    @patch("Main.superbet_api")
    def test_uses_exact_event_link_not_generic_homepage(self, api):
        data=payload("Liczba rzutów rożnych",[selection("Powyżej 8.5")])
        data["events"][0]["fixture"]["event_url"]="https://superbet.pl/zaklady-bukmacherskie/pilka-nozna/test-event"
        api.return_value=data
        self.assertEqual(Main.superbet_match_url(123,"Home","Away",0),
                         "https://superbet.pl/zaklady-bukmacherskie/pilka-nozna/test-event")

    @patch("Main.superbet_api")
    def test_builds_exact_superbet_event_route_when_api_omits_url(self, api):
        api.return_value=payload("Liczba rzutów rożnych",[selection("Powyżej 8.5")])
        self.assertEqual(Main.superbet_match_url(123,"Botosani","Otelul Galati",0),
                         "https://superbet.pl/kursy/pilka-nozna/botosani-vs-otelul-galati-123")

    @patch("Main.load_matchday_results")
    @patch("Main.recent_team_ft_corner_history")
    def test_rejects_weak_match_and_ranks_strong_match(self, history, rows):
        rows.return_value=[{"date":"2026-11-08"}]
        strong=[("2026-11-01",5,5,10)]*8
        history.side_effect=[strong,strong]
        score,detail=Main.ft_matchday_candidate_profile("Test League","Home","Away",1794000000)
        self.assertIsNotNone(score)
        self.assertIn("Over 8.5: 100%",detail)
        weak=[("2026-11-01",2,4,6)]*8
        history.side_effect=[weak,weak]
        score,detail=Main.ft_matchday_candidate_profile("Test League","Home","Away",1794000000)
        self.assertIsNone(score)


    def test_league_corner_profile_uses_recent_ft_totals_and_over85_rate(self):
        rows=[{"date":f"2026-10-{day:02d}","home_corners":"6","away_corners":"4"}
              for day in range(1,26)]
        average,hit_rate,count=Main.league_ft_corner_profile(rows,"2026-11-01")
        self.assertEqual(count,25)
        self.assertEqual(average,10.0)
        self.assertEqual(hit_rate,1.0)

    def test_league_corner_profile_ignores_fixture_day_and_future(self):
        rows=[{"date":f"2026-10-{day:02d}","home_corners":"5","away_corners":"5"}
              for day in range(1,21)]
        rows += [{"date":"2026-11-01","home_corners":"0","away_corners":"0"},
                 {"date":"2026-11-02","home_corners":"0","away_corners":"0"}]
        average,hit_rate,count=Main.league_ft_corner_profile(rows,"2026-11-01")
        self.assertEqual(count,20)
        self.assertEqual(average,10.0)
        self.assertEqual(hit_rate,1.0)


if __name__=="__main__":
    unittest.main()
