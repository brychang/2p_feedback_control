"""Tests for experiment folder diagnostics, stim detection, and plots."""

import os
import tempfile
import unittest

import numpy as np

from experiment_diagnostics import (
    compute_diagnostics,
    detect_stim_events,
    find_latest_experiment,
    load_experiment,
    pearson_r,
    save_diagnostic_plots,
)
from experiment_logging import append_power_sample, close_power_logs, open_power_logs, start_experiment


def _write_experiment(folder_parent, timestamp, stim, feedback, etl=None, shutter=None, times=None):
    session = start_experiment(
        folder_parent,
        {
            "target_power": 2.0,
            "feedback_tolerance": 0.1,
            "degrees_to_move": 0.1,
            "powermeter": "1",
            "sample_seconds": 4,
            "starting_degree": 45.0,
        },
        timestamp=timestamp,
    )
    open_power_logs(session)
    try:
        for i, (s, f) in enumerate(zip(stim, feedback)):
            append_power_sample(
                session,
                s,
                f,
                etl_v=None if etl is None else etl[i],
                shutter_v=None if shutter is None else shutter[i],
                t_s=None if times is None else times[i],
            )
    finally:
        close_power_logs(session)
    return session["experiment_dir"]


class TestFindLatestAndLoad(unittest.TestCase):
    def test_find_latest_uses_folder_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_experiment(tmp, "20260821_120000", [1.0], [2.0])
            newest = _write_experiment(tmp, "20260821_180521", [1.5], [2.5])
            self.assertEqual(find_latest_experiment(tmp), newest)

    def test_load_aligns_etl_and_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write_experiment(
                tmp,
                "20260821_180521",
                stim=[1.0, 1.1],
                feedback=[2.0, 2.1],
                etl=[3.0, 3.1],
                times=[0.0, 0.01],
            )
            data = load_experiment(folder)
            np.testing.assert_allclose(data["stim"], [1.0, 1.1])
            np.testing.assert_allclose(data["etl"], [3.0, 3.1])
            np.testing.assert_allclose(data["time"], [0.0, 0.01])
            self.assertFalse(data["time_is_sample_index"])


class TestStimDetection(unittest.TestCase):
    def test_detects_pulses_from_stim_power(self):
        stim = np.array([0.05] * 20 + [2.0] * 8 + [0.05] * 20 + [2.1] * 8 + [0.04] * 20)
        feedback = np.full_like(stim, 1.5)
        times = np.arange(len(stim)) * 0.01
        data = {
            "n": len(stim),
            "stim": stim,
            "feedback": feedback,
            "etl": None,
            "shutter": None,
            "time": times,
            "time_is_sample_index": False,
            "folder": "synthetic",
        }
        detection = detect_stim_events(data, min_gap_s=0.02, min_samples=2)
        self.assertEqual(detection["method"], "stim_power_otsu")
        self.assertEqual(detection["n_events"], 2)
        self.assertGreater(detection["events"][0]["stim_mean"], 1.0)

    def test_prefers_shutter_when_it_has_a_clear_high_state(self):
        n = 50
        stim = np.linspace(1.0, 1.2, n)
        shutter = np.array([0.0] * 20 + [4.8] * 10 + [0.0] * 20)
        data = {
            "n": n,
            "stim": stim,
            "feedback": np.ones(n),
            "etl": np.ones(n),
            "shutter": shutter,
            "time": np.arange(n) * 0.01,
            "time_is_sample_index": False,
            "folder": "synthetic",
        }
        detection = detect_stim_events(data)
        self.assertEqual(detection["method"], "shutter_voltage")
        self.assertEqual(detection["n_events"], 1)
        self.assertEqual(detection["events"][0]["start_index"], 20)


class TestCorrelationAndPlots(unittest.TestCase):
    def test_pearson_and_stim_etl_correlation(self):
        rng = np.random.default_rng(0)
        etl = np.linspace(0, 5, 80)
        stim = 0.4 * etl + 0.05 * rng.normal(size=80)
        self.assertGreater(pearson_r(stim, etl), 0.9)

        shutter = np.zeros(80)
        shutter[10:18] = 5
        shutter[40:48] = 5
        feedback = 2.0 + 0.01 * rng.normal(size=80)
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write_experiment(
                tmp,
                "20260821_180521",
                stim=list(stim),
                feedback=list(feedback),
                etl=list(etl),
                shutter=list(shutter),
                times=list(np.arange(80) * 0.01),
            )
            data = load_experiment(folder)
            diagnostics = compute_diagnostics(data)
            self.assertEqual(diagnostics["detection"]["n_events"], 2)
            self.assertGreater(diagnostics["r_stim_etl_all"], 0.8)
            out = os.path.join(folder, "diagnostics")
            saved, skipped = save_diagnostic_plots(data, diagnostics, out)
            self.assertEqual(skipped, [])
            names = {os.path.basename(path) for path in saved}
            self.assertIn("histogram_stim_power_all.png", names)
            self.assertIn("histogram_stim_power_during_stims.png", names)
            self.assertIn("histogram_stim_event_means.png", names)
            self.assertIn("stim_detection_timeline.png", names)
            self.assertIn("histogram_feedback_power.png", names)
            self.assertIn("variance_std_comparison.png", names)
            self.assertIn("correlation_stim_vs_etl.png", names)
            self.assertIn("correlation_stim_vs_feedback_at_stim.png", names)
            self.assertIn("diagnostics_summary.txt", names)
            for path in saved:
                self.assertGreater(os.path.getsize(path), 0)


class TestMissingETL(unittest.TestCase):
    def test_skips_etl_plot_when_readings_are_nan(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write_experiment(
                tmp,
                "20260821_180521",
                stim=[1.0, 1.2, 0.9, 1.1],
                feedback=[2.0, 2.1, 1.9, 2.0],
                etl=None,
                times=[0.0, 0.01, 0.02, 0.03],
            )
            data = load_experiment(folder)
            self.assertFalse(data["has_etl"])
            diagnostics = compute_diagnostics(data)
            self.assertFalse(diagnostics["has_etl"])
            saved, skipped = save_diagnostic_plots(
                data, diagnostics, os.path.join(folder, "diagnostics")
            )
            names = {os.path.basename(path) for path in saved}
            self.assertNotIn("correlation_stim_vs_etl.png", names)
            self.assertTrue(any("no ETL" in item for item in skipped))
            self.assertIn("histogram_feedback_power.png", names)
            summary_path = os.path.join(folder, "diagnostics", "diagnostics_summary.txt")
            with open(summary_path, encoding="utf-8") as f:
                summary = f.read()
            self.assertIn("skipped (no ETL connected", summary)

    def test_skips_stim_plots_when_stim_is_nan(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write_experiment(
                tmp,
                "20260821_180521",
                stim=[float("nan"), float("nan")],
                feedback=[2.0, 2.1],
                etl=None,
                times=[0.0, 0.01],
            )
            data = load_experiment(folder)
            self.assertFalse(data["has_stim"])
            diagnostics = compute_diagnostics(data)
            saved, skipped = save_diagnostic_plots(
                data, diagnostics, os.path.join(folder, "diagnostics")
            )
            names = {os.path.basename(path) for path in saved}
            self.assertNotIn("histogram_stim_power_all.png", names)
            self.assertIn("histogram_feedback_power.png", names)
            self.assertTrue(any("stim" in item.lower() for item in skipped))


if __name__ == "__main__":
    unittest.main()
