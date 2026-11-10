import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Main as bot


class FTCornersPrematchTests(unittest.TestCase):
    def test_standard_over_95_requires_average_108_and_odds_170(self):
        self.assertIsNone(bot.select_ft_corner_scenario(10.79, {9.5: 1.90}))
        self.assertIsNone(bot.select_ft_corner_scenario(10.8, {9.5: 1.69}))
        self.assertEqual(
            bot.select_ft_corner_scenario(10.8, {9.5: 1.70}),
            {"line": 9.5, "odds": 1.70, "scenario": "A"},
        )

    def test_alternative_over_105_requires_average_above_125_and_odds_185(self):
        self.assertIsNone(bot.select_ft_corner_scenario(12.5, {10.5: 1.90}))
        self.assertIsNone(bot.select_ft_corner_scenario(12.6, {10.5: 1.84}))
        self.assertEqual(
            bot.select_ft_corner_scenario(12.6, {10.5: 1.85}),
            {"line": 10.5, "odds": 1.85, "scenario": "B"},
        )

    def test_standard_scenario_has_priority_when_both_qualify(self):
        self.assertEqual(
            bot.select_ft_corner_scenario(13.0, {9.5: 1.75, 10.5: 1.95}),
            {"line": 9.5, "odds": 1.75, "scenario": "A"},
        )

    def test_low_standard_price_can_only_be_replaced_by_valid_alternative(self):
        self.assertEqual(
            bot.select_ft_corner_scenario(12.6, {9.5: 1.55, 10.5: 1.90}),
            {"line": 10.5, "odds": 1.90, "scenario": "B"},
        )
        self.assertIsNone(bot.select_ft_corner_scenario(11.2, {9.5: 1.55}))

    def test_prematch_rules_and_full_day_scan(self):
        self.assertEqual(
            (bot.FT_STANDARD_LINE, bot.FT_STANDARD_MIN_ODDS, bot.FT_STANDARD_MIN_AVG),
            (9.5, 1.70, 10.8),
        )
        self.assertEqual(
            (bot.FT_ALT_LINE, bot.FT_ALT_MIN_ODDS, bot.FT_ALT_MIN_AVG),
            (10.5, 1.85, 12.5),
        )
        self.assertFalse(hasattr(bot, "FT_KICKOFF_WINDOW_MINUTES"))


if __name__ == "__main__":
    unittest.main()
