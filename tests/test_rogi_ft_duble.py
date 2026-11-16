import unittest
from RogiFTDuble import MIN_LEG_ODDS, MAX_DOUBLES_PER_DAY, LEGS_PER_DOUBLE, FT_LINES


class StrategyRulesTests(unittest.TestCase):
    def test_rules_are_ft_over_only_and_minimum_odds(self):
        self.assertEqual(MIN_LEG_ODDS, 1.80)
        self.assertEqual(LEGS_PER_DOUBLE, 2)
        self.assertEqual(MAX_DOUBLES_PER_DAY, 2)
        self.assertTrue(all(line >= 9.5 for line in FT_LINES))

    def test_daily_cap_is_two_doubles(self):
        self.assertEqual(MAX_DOUBLES_PER_DAY * LEGS_PER_DOUBLE, 4)


if __name__ == "__main__":
    unittest.main()
