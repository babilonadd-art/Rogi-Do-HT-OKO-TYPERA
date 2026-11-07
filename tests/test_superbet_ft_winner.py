import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Main


def event_payload(market_name, odds, market_status=1, inplay="NOT_STARTED",
                  offer_state_status=None):
    return {"events": [{
        "id": 123,
        "fixture": {"offer_state_status": offer_state_status or {"1": 1}},
        "inplay_stats_metadata": {"status": inplay},
        "markets": [{"name": market_name, "status": market_status, "odds": odds}],
    }]}


def odd(name, price, status=1, display=True, offer_state_id=1):
    return {
        "name": name, "price": price, "status": status, "display": display,
        "metadata": {"name": name, "offer_state_id": offer_state_id},
    }


class SuperbetFullTimeWinnerTests(unittest.TestCase):
    def check_market(self, market_name, odds):
        with patch("Main.superbet_api") as api, patch(
            "Main.winner_form_passes", return_value=(True, "forma testowa")
        ) as form:
            api.return_value = event_payload(market_name, odds)
            result = Main.winner_market_selection(
                123, "Team A", "Team B", "Test League", 1790000000
            )
        return result, api, form

    def test_reads_home_win_from_active_full_time_1x2_market(self):
        result, api, form = self.check_market("Wynik meczu 1X2", [
            odd("1", 3.20), odd("X", 3.10), odd("2", 2.25)
        ])
        self.assertEqual(result, {
            "selection": "1", "odds": 3.20, "form": "forma testowa"
        })
        api.assert_called_once_with("/v3/pl-PL/events", {"events": "123"})
        form.assert_any_call("Test League", "Team A", "Team B", 1790000000, "1")

    def test_reads_away_win_from_team_label(self):
        result, _, _ = self.check_market("Wynik meczu", [
            odd("Remis", 3.10), odd("Goście", 2.40)
        ])
        self.assertEqual(result, {
            "selection": "2", "odds": 2.40, "form": "forma testowa"
        })

    @patch("Main.superbet_api")
    def test_rejects_half_time_and_double_chance_markets(self, api):
        for market in ("1. połowa - wynik meczu", "Podwójna szansa 1X"):
            api.return_value = event_payload(market, [odd("1", 2.20)])
            with self.subTest(market=market):
                self.assertIsNone(Main.winner_market_selection(
                    123, "Team A", "Team B", "Test League", 1790000000
                ))

    @patch("Main.superbet_api")
    def test_rejects_inactive_hidden_and_live_selections(self, api):
        cases = [
            event_payload("Wynik meczu", [odd("1", 2.20, status=0)]),
            event_payload("Wynik meczu", [odd("1", 2.20, display=False)]),
            event_payload("Wynik meczu", [odd("1", 2.20, offer_state_id=2)]),
            event_payload("Wynik meczu", [odd("1", 2.20)], inplay="IN_PLAY"),
            event_payload("Wynik meczu", [odd("1", 2.20)], market_status=0),
        ]
        for payload in cases:
            api.return_value = payload
            with self.subTest(payload=payload):
                self.assertIsNone(Main.winner_market_selection(
                    123, "Team A", "Team B", "Test League", 1790000000
                ))

    @patch("Main.superbet_api")
    @patch("Main.winner_form_passes", return_value=(False, "za słaba forma"))
    def test_rejects_selection_when_team_form_does_not_pass(self, form, api):
        api.return_value = event_payload("Wynik meczu", [odd("1", 2.20)])
        self.assertIsNone(Main.winner_market_selection(
            123, "Team A", "Team B", "Test League", 1790000000
        ))
        form.assert_called_once()

    def test_common_team_name_aliases_match_open_matchday_spelling(self):
        self.assertGreaterEqual(Main.team_similarity("Manchester City", "Man City"), 0.82)
        self.assertGreaterEqual(Main.team_similarity("Manchester United", "Man Utd"), 0.82)
        self.assertGreaterEqual(Main.team_similarity("Nottingham Forest", "Nott'm Forest"), 0.82)
        self.assertGreaterEqual(Main.team_similarity("Tottenham Hotspur", "Spurs"), 0.82)
        self.assertGreaterEqual(Main.team_similarity("Istanbul Basaksehir", "Basaksehir"), 0.82)

    def test_matchday_form_reads_recent_full_time_results(self):
        rows = [
            {"date": "2026-09-05", "home": "Team A", "away": "Other 1", "ft_home": "2", "ft_away": "0"},
            {"date": "2026-09-04", "home": "Team A", "away": "Other 2", "ft_home": "1", "ft_away": "0"},
            {"date": "2026-09-03", "home": "Other 3", "away": "Team A", "ft_home": "0", "ft_away": "1"},
            {"date": "2026-09-02", "home": "Team A", "away": "Other 4", "ft_home": "1", "ft_away": "1"},
            {"date": "2026-09-01", "home": "Team A", "away": "Other 5", "ft_home": "0", "ft_away": "2"},
        ]
        with patch("Main.load_matchday_results", return_value=rows):
            form, detail = Main.matchday_recent_form(
                "Polska Ekstraklasa", "Team A", 1790000000
            )
        self.assertEqual(form, {"wins": 3, "draws": 1, "losses": 1})
        self.assertIn("Open Matchday", detail)

    def test_winner_form_uses_open_matchday_for_broader_leagues(self):
        rows = []
        for day, score in zip(
            ["2026-09-05", "2026-09-04", "2026-09-03", "2026-09-02", "2026-09-01"],
            [(2, 0), (1, 0), (0, 1), (1, 1), (0, 2)],
        ):
            rows.append({
                "date": day, "home": "Team A", "away": "Other " + day,
                "ft_home": str(score[0]), "ft_away": str(score[1]),
            })
        for day, score in zip(
            ["2026-09-05", "2026-09-04", "2026-09-03", "2026-09-02", "2026-09-01"],
            [(1, 0), (0, 1), (0, 2), (2, 0), (0, 1)],
        ):
            rows.append({
                "date": day, "home": "Team B", "away": "Other B " + day,
                "ft_home": str(score[0]), "ft_away": str(score[1]),
            })
        with patch("Main.load_matchday_results", return_value=rows):
            passed, detail = Main.winner_form_passes(
                "Polska Ekstraklasa", "Team A", "Team B", 1790000000, "1"
            )
        self.assertTrue(passed, detail)
        self.assertIn("Open Matchday", detail)

    def test_four_leg_coupon_total_must_be_5_to_10(self):
        # 1.70 x 1.70 x 1.70 x 1.70 = 8.35, inside the configured range.
        candidates = [
            {"event_id": i, "odds": 1.70, "start_ts": i, "priority": 1}
            for i in range(4)
        ]
        result = Main.choose_four_leg_coupon(candidates)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result[1], 5.0)
        self.assertLessEqual(result[1], 10.0)

    def test_does_not_force_coupon_when_total_outside_range(self):
        candidates = [
            {"event_id": i, "odds": 1.2, "start_ts": i, "priority": 1}
            for i in range(4)
        ]
        self.assertIsNone(Main.choose_four_leg_coupon(candidates))


if __name__ == "__main__":
    unittest.main()
