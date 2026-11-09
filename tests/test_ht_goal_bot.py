import unittest
import HTGoalBot as bot


class HTGoalBotTests(unittest.TestCase):
    def test_team_normalization(self):
        self.assertEqual(bot.normalize_team("FC Bayern München"), "bayernmunchen")
        self.assertGreaterEqual(bot.team_similarity("Manchester Utd", "Manchester Utd"), 0.99)

    def test_momentum_normalized_and_integer_scales(self):
        self.assertTrue(bot.momentum_is_strong([
            {"minute": 16, "value": 0.25},
            {"minute": 17, "value": 0.30},
            {"minute": 18, "value": 0.22},
        ]))
        self.assertTrue(bot.momentum_is_strong([
            {"minute": 16, "value": -25},
            {"minute": 17, "value": -30},
            {"minute": 18, "value": -22},
        ]))
        self.assertFalse(bot.momentum_is_strong([
            {"minute": 16, "value": 0.25},
            {"minute": 17, "value": 0.10},
            {"minute": 18, "value": 0.22},
        ]))

    def test_momentum_requires_three_consecutive_minutes(self):
        self.assertFalse(bot.momentum_is_strong([
            {"minute": 16, "value": 0.5},
            {"minute": 18, "value": 0.5},
            {"minute": 19, "value": 0.5},
        ]))

    def test_shots_only_first_half_and_first_20_minutes(self):
        payload = {"periods": [
            {"period": "FirstHalf", "shots": [
                {"minute": 4, "is_on_target": True},
                {"minute": 19, "is_on_target": False},
                {"minute": 21, "is_on_target": True},
            ]},
            {"period": "SecondHalf", "shots": [{"minute": 50, "is_on_target": True}]},
        ]}
        self.assertEqual(len(bot.unwrap_shots(payload)), 2)

    def test_corners_from_live_stats(self):
        self.assertEqual(bot.corners_from_live({"stats": [
            {"key": "corners", "home": "1", "away": "2"}
        ]}), 3)
        self.assertIsNone(bot.corners_from_live({"stats": []}))

    def test_goal_minute(self):
        self.assertEqual(bot.first_goal_minute([
            {"event_type": "Goal", "minute": 7},
            {"event_type": "Substitution", "minute": 61},
        ]), 7)
        self.assertIsNone(bot.first_goal_minute([]))

    def test_target_lines(self):
        self.assertEqual((bot.MINUTE_MIN, bot.MINUTE_MAX), (22, 26))
        self.assertEqual((bot.ODDS_MIN, bot.ODDS_MAX), (1.80, 2.00))


if __name__ == "__main__":
    unittest.main()
