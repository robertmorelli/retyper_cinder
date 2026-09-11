"""Regression checks for stable mask positions and preserved experiment results."""
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarking.grow_exp import grow_experiment
from benchmarking.run_exp import main, select_level_masks
from utilities.experiments import (
    initialize_experiment_state, load_experiment, save_new_experiment,
    validate_experiment,
)


class ExperimentGrowthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.patch = patch('utilities.experiments.granularity_map',
                           return_value={'a': 'annotation', 'b': 'annotation'})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        plan = {name: {variant: [0, 1, 3, 7, 15]
                       for variant in ('advanced', 'shallow', 'untyped')}
                for name in ('a', 'b')}
        state = initialize_experiment_state(plan)
        save_new_experiment(self.path, plan, state['typechecks'], state['results'])

    def test_inclusive_positions_per_level(self):
        masks = [0, 1, 3, 7, 15, 2, 4, 8, 5, 6, 9, 11, 13, 14]
        self.assertEqual(select_level_masks(masks, 2, 5),
                         [2, 4, 8, 5, 6, 9, 11, 13, 14])
        self.assertEqual(select_level_masks(masks, 1, 1), [0, 1, 3, 7, 15])

    def test_growth_preserves_results_and_positions(self):
        experiment = load_experiment(self.path)
        experiment.samples_for('a', 'advanced', 1).append(1.0)
        experiment.samples_for('b', 'shallow', 3).append(2.0)
        experiment.save_results()
        experiment.mark_typechecked('a', 'advanced', 1)
        experiment.mark_typechecked('b', 'shallow', 3)
        experiment.save_typechecks()
        grow_experiment(self.path, 3, random.Random(1))
        current = load_experiment(self.path)
        validate_experiment(current)
        self.assertEqual(current.samples_for('a', 'advanced', 1), [1.0])
        self.assertEqual(current.samples_for('b', 'shallow', 3), [2.0])
        self.assertTrue(current.typechecks['a']['advanced']['1'])
        self.assertTrue(current.typechecks['b']['shallow']['3'])
        old = select_level_masks(current.plan['a']['advanced'], 1, 3)
        grow_experiment(self.path, 100, random.Random(2))
        current = load_experiment(self.path)
        self.assertEqual(select_level_masks(current.plan['a']['advanced'], 1, 3), old)
        self.assertEqual(set(current.plan['a']['advanced']), set(range(16)))
        self.assertEqual(grow_experiment(self.path, 2), 0)

    def test_cli_runs_all_benchmarks_and_variants(self):
        grow_experiment(self.path, 5, random.Random(3))
        with patch('sys.argv', ['run_exp.py', '--which', '2', '5']), \
             patch('benchmarking.run_exp.experiment_for', return_value=self.path), \
             patch('benchmarking.run_exp.measure_benchmark', return_value=[1.0] * 8) as measure, \
             patch('builtins.print'):
            main()
        # 3 + 4 + 3 masks at the three interior levels, per variant/benchmark.
        self.assertEqual(measure.call_count, 60)
        self.assertEqual({call.args[1] for call in measure.call_args_list}, {'a', 'b'})
        self.assertEqual({call.args[2] for call in measure.call_args_list},
                         {'advanced', 'shallow', 'untyped'})


if __name__ == '__main__':
    unittest.main()
