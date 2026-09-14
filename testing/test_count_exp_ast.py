"""Tests for sharding and merging experiment AST counts."""

from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from benchmarking.count_exp_ast import count_mask, merge_shards, planned_jobs


class CountExperimentAstTests(TestCase):
    def test_planned_jobs_preserve_plan_order(self):
        experiment = SimpleNamespace(
            plan={"bench": {"advanced": [0, 2], "untyped": [0]}},
            granularities={"bench": "annotation"},
        )
        self.assertEqual(
            planned_jobs(experiment),
            [
                ("bench", "advanced", 0, "annotation"),
                ("bench", "advanced", 2, "annotation"),
                ("bench", "untyped", 0, "annotation"),
            ],
        )

    def test_count_mask_excludes_import_subtree(self):
        with patch(
            "benchmarking.count_exp_ast.detyped_benchmark_source",
            return_value="import package as alias\nx = 1\n",
        ):
            _, _, _, count = count_mask(("bench", "advanced", 0, "annotation"))
        self.assertEqual(count, 5)

    def test_merge_requires_every_planned_mask(self):
        experiment = SimpleNamespace(plan={"bench": {"advanced": [0, 1]}})
        with patch(
            "benchmarking.count_exp_ast.read_json",
            return_value={"bench": {"advanced": {"0": 5, "1": 4}}},
        ):
            self.assertEqual(
                merge_shards(experiment, [Path("shard.json")]),
                {"bench": {"advanced": {"0": 5, "1": 4}}},
            )
