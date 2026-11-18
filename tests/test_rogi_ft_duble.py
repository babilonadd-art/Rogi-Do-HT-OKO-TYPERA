import unittest
from unittest.mock import patch
import Main
from RogiFTDuble import MIN_LEG_ODDS, MAX_LEG_ODDS, MAX_DOUBLES_PER_DAY, LEGS_PER_DOUBLE, FT_LINES, _placeholder_team, daily_quota_count
from Main import superbet_event_is_prematch


class StrategyRulesTests(unittest.TestCase):
    def test_rules_are_ft_over_only_and_minimum_odds(self):
        self.assertEqual(MIN_LEG_ODDS, 1.60)
        self.assertEqual(MAX_LEG_ODDS, 2.20)
        self.assertEqual(LEGS_PER_DOUBLE, 2)
        self.assertEqual(MAX_DOUBLES_PER_DAY, 2)
        self.assertEqual(FT_LINES, (8.5, 9.5))

    def test_placeholder_team_names_are_rejected(self):
        for name in ("A", "B", "Team A", "Team B", "Drużyna A", "Drużyna B", "Unknown", "TBD"):
            self.assertTrue(_placeholder_team(name))
        self.assertFalse(_placeholder_team("Kuwejt"))
        self.assertFalse(_placeholder_team("Honduras"))

    def test_daily_cap_is_two_doubles(self):
        self.assertEqual(MAX_DOUBLES_PER_DAY * LEGS_PER_DOUBLE, 4)

    def test_quota_reset_preserves_old_history_and_unlocks_two_new_doubles(self):
        import sqlite3
        from datetime import datetime
        from zoneinfo import ZoneInfo

        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE ft_doubles(coupon_id TEXT, day TEXT, sent_at TEXT)')
        db.execute('CREATE TABLE ft_daily_quota_resets(day TEXT PRIMARY KEY, reset_at TEXT)')
        day = '2026-11-17'
        db.executemany(
            'INSERT INTO ft_doubles VALUES(?,?,?)',
            [('old-1', day, '2026-11-17T12:00:00+01:00'),
             ('old-2', day, '2026-11-17T13:00:00+01:00')],
        )
        now = datetime(2026, 11, 17, 20, 0, tzinfo=ZoneInfo('Europe/Warsaw'))
        db.execute('INSERT INTO ft_daily_quota_resets VALUES(?,?)', (day, now.isoformat()))
        self.assertEqual(daily_quota_count(db, day), 0)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM ft_doubles').fetchone()[0], 2)
        db.execute('INSERT INTO ft_doubles VALUES(?,?,?)',
                   ('new-1', day, '2026-11-17T20:01:00+01:00'))
        self.assertEqual(daily_quota_count(db, day), 1)
        db.close()
    def test_superbet_football_sport_id(self):
        self.assertTrue(superbet_event_is_prematch({
            "fixture": {"sport_id": 5, "offer_state_status": {"1": 1}},
            "inplay_stats_metadata": {"status": "NOT_STARTED"},
        }))
        self.assertFalse(superbet_event_is_prematch({
            "fixture": {"sport_id": 4, "offer_state_status": {"1": 1}},
            "inplay_stats_metadata": {"status": "NOT_STARTED"},
        }))

    @patch("Main.superbet_api")
    def test_all_ft_over_lines_are_read_with_one_detail_request(self, api):
        api.return_value = {"events": [{
            "id": 123,
            "fixture": {"event_name": "Coritiba·Santos", "offer_state_status": {"1": 1}},
            "inplay_stats_metadata": {"status": "NOT_STARTED"},
            "markets": [{
                "name": "Liczba rzutów rożnych",
                "status": 1,
                "odds": [
                    {"name": "Powyżej 9.5", "price": 1.55, "status": 1,
                     "display": True, "metadata": {"special_bet_value": "9.5", "offer_state_id": 1}},
                    {"name": "Powyżej 10.5", "price": 1.90, "status": 1,
                     "display": True, "metadata": {"special_bet_value": "10.5", "offer_state_id": 1}},
                    {"name": "Powyżej 11.5", "price": 2.20, "status": 1,
                     "display": True, "metadata": {"special_bet_value": "11.5", "offer_state_id": 1}},
                ],
            }, {
                "name": "Coritiba - liczba rzutów rożnych",
                "status": 1,
                "odds": [{
                    "name": "Powyżej 12.5", "price": 2.80, "status": 1,
                    "display": True, "metadata": {"special_bet_value": "12.5", "offer_state_id": 1},
                }],
            }],
        }]}
        result = Main.superbet_ft_corners_over_prices(123, lines=FT_LINES, min_odds=1.80)
        self.assertEqual(result, {10.5: 1.90, 11.5: 2.20})
        api.assert_called_once_with("/v3/pl-PL/events", {"events": "123"})


if __name__ == "__main__":
    unittest.main()
