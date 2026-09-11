"""Regression checks for benchmark-variant unit counting."""

import unittest
from unittest.mock import patch

from utilities.graph import count_benchmark_units


class BenchmarkUnitCountTests(unittest.TestCase):
    def test_untyped_is_one_baseline_with_zero_selectable_units(self):
        with patch("utilities.graph.load_bench") as load:
            self.assertEqual(count_benchmark_units("bench", "untyped", "annotation"), 0)
        load.assert_not_called()

    def test_shallow_counts_the_shallow_source(self):
        with patch("utilities.graph.load_bench", return_value="source") as load, \
             patch("utilities.graph.count_units", return_value=7) as count:
            self.assertEqual(count_benchmark_units("bench", "shallow", "annotation"), 7)
        load.assert_called_once_with("bench", "shallow")
        count.assert_called_once_with("source", "annotation")


if __name__ == "__main__":
    unittest.main()
