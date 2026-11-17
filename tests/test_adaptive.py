import unittest

from adaptive_discovery import search_budgets, focus_styles


class AdaptiveDiscoveryTests(unittest.TestCase):
    def test_budget_is_bounded_and_no_more_than_user_requested(self):
        for n in range(4, 61):
            for adaptive in (False, True):
                wide, deep = search_budgets(n, adaptive)
                self.assertEqual(wide + deep, n)
                self.assertGreaterEqual(wide, 4)
                self.assertEqual(deep, 0 if (not adaptive or n < 8) else deep)
                if adaptive and n >= 8:
                    self.assertGreater(deep, 0)
        for n in (0, 3, 61):
            with self.assertRaises(ValueError): search_budgets(n)

    def test_focus_uses_actual_scores_not_tag_order(self):
        rows = [
            {'source_style': 'metal', 'score': 50},
            {'source_style': 'hiphop', 'score': 99},
            {'source_style': 'electronic', 'score': 95},
            {'source_style': 'hiphop', 'score': 97},
        ]
        self.assertEqual(focus_styles(rows, ('metal', 'hiphop', 'electronic')), ('hiphop', 'electronic'))
        self.assertEqual(focus_styles([], ('metal',)), ())

    def test_unrecognised_tags_are_not_used_as_queries(self):
        rows = [{'source_style': 'bad-unknown-style', 'score': 100}, {'source_style': 'rock', 'score': 67}]
        self.assertEqual(focus_styles(rows, ('rock','metal')), ('rock',))


if __name__ == '__main__': unittest.main()
