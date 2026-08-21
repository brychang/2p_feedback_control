"""Unit tests for hold-loop strategies and ranking (no hardware)."""

import os
import random
import unittest

from feedback_strategies import (
    BACKWARD,
    FORWARD,
    HOLD,
    AdaptiveStrategy,
    ConfirmThenMoveStrategy,
    DriftingPowerPlant,
    FixedStepStrategy,
    NoControlStrategy,
    ProportionalStrategy,
    ReversalDampingStrategy,
    aggregate_metrics,
    format_results_table,
    make_strategies,
    rank_strategies,
    run_hold_cycles,
    summarize_records,
)


class TestFixedAndProportional(unittest.TestCase):
    def test_fixed_step_holds_inside_deadband(self):
        strategy = FixedStepStrategy(0.5)
        self.assertEqual(strategy.decide(2.05, 2.0, 0.1)[0], HOLD)

    def test_fixed_step_uses_full_step_when_outside_band(self):
        strategy = FixedStepStrategy(0.5)
        direction, step = strategy.decide(3.0, 2.0, 0.1)
        self.assertEqual(direction, BACKWARD)
        self.assertEqual(step, 0.5)
        direction, step = strategy.decide(1.0, 2.0, 0.1)
        self.assertEqual(direction, FORWARD)
        self.assertEqual(step, 0.5)

    def test_proportional_uses_smaller_step_near_target(self):
        strategy = ProportionalStrategy(max_step_deg=0.5, min_step_deg=0.05)
        far_dir, far_step = strategy.decide(4.0, 2.0, 0.1)
        near_dir, near_step = strategy.decide(2.16, 2.0, 0.1)
        self.assertEqual(far_dir, BACKWARD)
        self.assertEqual(near_dir, BACKWARD)
        self.assertEqual(far_step, 0.5)
        self.assertLess(near_step, far_step)
        self.assertGreaterEqual(near_step, 0.05)


class TestDampingAndConfirm(unittest.TestCase):
    def test_reversal_halves_step(self):
        strategy = ReversalDampingStrategy(max_step_deg=0.8, min_step_deg=0.05)
        _, first = strategy.decide(3.0, 2.0, 0.1)
        self.assertEqual(first, 0.8)
        _, second = strategy.decide(1.0, 2.0, 0.1)
        self.assertAlmostEqual(second, 0.4)

    def test_adaptive_cuts_step_on_reversal(self):
        strategy = AdaptiveStrategy(max_step_deg=0.8, min_step_deg=0.05)
        _, first = strategy.decide(4.0, 2.0, 0.1)
        _, second = strategy.decide(0.0, 2.0, 0.1)
        self.assertLess(second, first)
        self.assertGreaterEqual(second, 0.05)

    def test_confirm_then_move_ignores_first_excursion(self):
        strategy = ConfirmThenMoveStrategy(0.5, confirms=2)
        direction, step = strategy.decide(3.0, 2.0, 0.1)
        self.assertEqual(direction, HOLD)
        self.assertEqual(step, 0.0)
        direction, step = strategy.decide(3.0, 2.0, 0.1)
        self.assertEqual(direction, BACKWARD)
        self.assertEqual(step, 0.5)


class TestPlantComparison(unittest.TestCase):
    def test_large_step_adaptive_is_more_stable_than_fixed(self):
        target = 2.0
        tolerance = 0.1
        max_step = 0.5
        min_step = 0.05
        n_cycles = 80

        def run(strategy_factory, seed):
            plant = DriftingPowerPlant(
                power_mw=target,
                mw_per_deg=1.0,
                drift_std=0.03,
                noise_std=0.01,
                rng=random.Random(seed),
            )
            strategy = strategy_factory()
            records = run_hold_cycles(strategy, plant, target, tolerance, n_cycles)
            return summarize_records(records, target, tolerance)

        seeds = [1, 2, 3, 4, 5]
        fixed_rows = [run(lambda: FixedStepStrategy(max_step), s) for s in seeds]
        adaptive_rows = [
            run(lambda: AdaptiveStrategy(max_step, min_step), s) for s in seeds
        ]
        fixed = aggregate_metrics(fixed_rows)
        adaptive = aggregate_metrics(adaptive_rows)

        self.assertGreater(adaptive["time_in_band_frac"], fixed["time_in_band_frac"])
        self.assertLess(adaptive["n_reversals"], fixed["n_reversals"])
        self.assertLess(adaptive["rms_error"], fixed["rms_error"])

    def test_rank_prefers_higher_time_in_band(self):
        ranked = rank_strategies(
            {
                "fixed_step": {
                    "time_in_band_frac": 0.4,
                    "rms_error": 0.2,
                    "n_reversals": 3,
                    "mean_abs_error": 0.1,
                    "max_abs_error": 0.5,
                    "n_moves": 12,
                },
                "adaptive": {
                    "time_in_band_frac": 0.8,
                    "rms_error": 0.3,
                    "n_reversals": 10,
                    "mean_abs_error": 0.2,
                    "max_abs_error": 0.6,
                    "n_moves": 8,
                },
            }
        )
        self.assertEqual(ranked[0][0], "adaptive")
        table = format_results_table(ranked)
        self.assertIn("adaptive", table)
        self.assertIn("fixed_step", table)

    def test_no_control_never_moves(self):
        plant = DriftingPowerPlant(2.0, rng=random.Random(0))
        records = run_hold_cycles(NoControlStrategy(), plant, 2.0, 0.1, 20)
        self.assertTrue(all(row["direction"] == HOLD for row in records))
        self.assertEqual(plant.position_deg, 0.0)

    def test_make_strategies_includes_baseline_and_adaptive(self):
        names = [s.name for s in make_strategies(0.5, 0.05)]
        self.assertEqual(names[0], "no_control")
        self.assertIn("fixed_step", names)
        self.assertIn("adaptive", names)


class TestCompareScriptSimulate(unittest.TestCase):
    def test_simulate_writes_ranked_summary(self):
        import tempfile

        from compare_feedback_strategies import main as compare_main

        with tempfile.TemporaryDirectory() as tmp:
            ranked = compare_main(
                [
                    "--simulate",
                    "--target",
                    "2",
                    "--tolerance",
                    "0.1",
                    "--max-step",
                    "0.5",
                    "--min-step",
                    "0.05",
                    "--cycles",
                    "40",
                    "--rounds",
                    "1",
                    "--output-dir",
                    tmp,
                ]
            )
            self.assertTrue(ranked)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "summary.txt")))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "metrics.csv")))
            with open(os.path.join(tmp, "summary.txt"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("BEST:", text)
            self.assertIn("adaptive", text)
            self.assertIn("fixed_step", text)


if __name__ == "__main__":
    unittest.main()
