"""Tests for timestamped experiment folders, logbook.txt, and live power-log writing."""

import os
import tempfile
import unittest
from datetime import datetime

from experiment_logging import (
    POWER_LOGS_ROOT_NAME,
    append_power_sample,
    close_power_logs,
    default_logs_root,
    experiment_folder_name,
    format_experiment_timestamp,
    open_power_logs,
    power_log_filenames,
    start_experiment,
)


EXAMPLE_WHEN = datetime(2026, 8, 21, 18, 5, 21)
EXAMPLE_TIMESTAMP = "20260821_180521"


def _sample_params():
    return {
        "target_power": 2.5,
        "feedback_tolerance": 0.05,
        "degrees_to_move": 0.1,
        "powermeter": "1",
        "sample_seconds": 4,
        "starting_degree": 45.0,
        "initial_tolerance": 0.0625,
        "testing_state": False,
    }


class TestExperimentTimestamp(unittest.TestCase):
    def test_timestamp_matches_yyyymmdd_hhmmss_format(self):
        self.assertEqual(format_experiment_timestamp(EXAMPLE_WHEN), EXAMPLE_TIMESTAMP)

    def test_timestamp_includes_underscore_and_seconds(self):
        stamp = format_experiment_timestamp(EXAMPLE_WHEN)
        self.assertEqual(stamp, "20260821_180521")
        self.assertEqual(len(stamp), 15)

    def test_folder_name_uses_feedback_control_prefix(self):
        self.assertEqual(
            experiment_folder_name(EXAMPLE_TIMESTAMP),
            "feedback_control_20260821_180521",
        )


class TestStartExperimentCreatesFolderAndLogbook(unittest.TestCase):
    def test_creates_folder_under_power_logs_experiments_when_inputs_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs_root = os.path.join(tmp, POWER_LOGS_ROOT_NAME)
            session = start_experiment(logs_root, _sample_params(), when=EXAMPLE_WHEN)

            expected_dir = os.path.join(
                logs_root, "feedback_control_20260821_180521"
            )
            self.assertEqual(session["experiment_dir"], expected_dir)
            self.assertTrue(os.path.isdir(expected_dir))
            self.assertEqual(session["timestamp"], EXAMPLE_TIMESTAMP)

    def test_creates_empty_power_log_files_at_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs_root = os.path.join(tmp, POWER_LOGS_ROOT_NAME)
            session = start_experiment(logs_root, _sample_params(), when=EXAMPLE_WHEN)

            self.assertTrue(os.path.isfile(session["logbook_path"]))
            self.assertEqual(os.path.basename(session["logbook_path"]), "logbook.txt")
            self.assertTrue(os.path.isfile(session["stim_log_path"]))
            self.assertTrue(os.path.isfile(session["feedback_log_path"]))
            self.assertEqual(os.path.getsize(session["stim_log_path"]), 0)
            self.assertEqual(os.path.getsize(session["feedback_log_path"]), 0)
            self.assertEqual(
                os.path.basename(session["stim_log_path"]),
                "stim_power_log_20260821_180521.txt",
            )
            self.assertEqual(
                os.path.basename(session["feedback_log_path"]),
                "feedback_power_log_20260821_180521.txt",
            )

    def test_logbook_contains_entered_parameters_sampling_average_and_starting_degree(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs_root = os.path.join(tmp, POWER_LOGS_ROOT_NAME)
            params = _sample_params()
            params["powermeter"] = "2"
            session = start_experiment(logs_root, params, when=EXAMPLE_WHEN)

            with open(session["logbook_path"], encoding="utf-8") as f:
                text = f.read()

            self.assertIn("Target power (mW): 2.5", text)
            self.assertIn("Tolerance (mW): 0.05", text)
            self.assertIn("Step size (deg): 0.1", text)
            self.assertIn("Powermeter: 2 (post-ETL)", text)
            self.assertIn("Sampling average (s): 4", text)
            self.assertIn("Starting degree: 45.0", text)
            self.assertIn("Initial tolerance (mW): 0.0625", text)
            self.assertIn("Testing state: False", text)
            self.assertIn("Timestamp: 20260821_180521", text)

    def test_logbook_labels_pre_mems_powermeter(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs_root = os.path.join(tmp, POWER_LOGS_ROOT_NAME)
            session = start_experiment(logs_root, _sample_params(), when=EXAMPLE_WHEN)
            with open(session["logbook_path"], encoding="utf-8") as f:
                text = f.read()
            self.assertIn("Powermeter: 1 (pre-MEMS)", text)

    def test_default_logs_root_is_outside_the_repo_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "Feedback_Control")
            os.makedirs(repo)
            fake_script = os.path.join(repo, "Feedback_Controller_Loop_20260821.py")
            self.assertEqual(
                default_logs_root(fake_script),
                os.path.join(tmp, POWER_LOGS_ROOT_NAME),
            )


class TestLivePowerLogWriting(unittest.TestCase):
    def test_samples_are_written_during_experiment_not_only_at_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs_root = os.path.join(tmp, POWER_LOGS_ROOT_NAME)
            session = start_experiment(logs_root, _sample_params(), when=EXAMPLE_WHEN)
            open_power_logs(session)
            try:
                append_power_sample(session, 1.1, 2.1, etl_v=3.3, shutter_v=0.0)
                with open(session["stim_log_path"], encoding="utf-8") as f:
                    self.assertEqual(f.read(), "1.1\n")
                with open(session["feedback_log_path"], encoding="utf-8") as f:
                    self.assertEqual(f.read(), "2.1\n")
                with open(session["etl_log_path"], encoding="utf-8") as f:
                    self.assertEqual(f.read(), "3.3\n")

                append_power_sample(session, 1.2, 2.2, etl_v=3.4, shutter_v=4.9)
                append_power_sample(session, 1.3, 2.3, etl_v=3.5, shutter_v=0.0)
            finally:
                close_power_logs(session)

            names = power_log_filenames(EXAMPLE_TIMESTAMP)
            self.assertEqual(os.path.basename(session["stim_log_path"]), names["stim"])
            self.assertEqual(os.path.basename(session["feedback_log_path"]), names["feedback"])
            with open(session["stim_log_path"], encoding="utf-8") as f:
                self.assertEqual(f.read(), "1.1\n1.2\n1.3\n")
            with open(session["feedback_log_path"], encoding="utf-8") as f:
                self.assertEqual(f.read(), "2.1\n2.2\n2.3\n")

    def test_full_input_then_live_write_then_quit_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs_root = os.path.join(tmp, POWER_LOGS_ROOT_NAME)
            session = start_experiment(logs_root, _sample_params(), when=EXAMPLE_WHEN)
            folder_files = sorted(os.listdir(session["experiment_dir"]))
            self.assertEqual(
                folder_files,
                [
                    "etl_voltage_log_20260821_180521.txt",
                    "feedback_power_log_20260821_180521.txt",
                    "logbook.txt",
                    "sample_time_log_20260821_180521.txt",
                    "shutter_voltage_log_20260821_180521.txt",
                    "stim_power_log_20260821_180521.txt",
                ],
            )

            open_power_logs(session)
            try:
                append_power_sample(session, 0.5, 1.5)
            finally:
                close_power_logs(session)

            with open(session["stim_log_path"], encoding="utf-8") as f:
                self.assertEqual(f.read(), "0.5\n")
            with open(session["feedback_log_path"], encoding="utf-8") as f:
                self.assertEqual(f.read(), "1.5\n")


class TestControllerWiring(unittest.TestCase):
    def test_controller_creates_session_after_inputs_and_writes_logs_during_run(self):
        controller_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "Feedback_Controller_Loop_20260821.py",
        )
        with open(controller_path, encoding="utf-8") as f:
            src = f.read()

        self.assertIn("from experiment_logging import", src)
        self.assertIn("start_experiment(", src)
        self.assertIn("append_power_sample(", src)
        self.assertIn("ETL_AIN", src)
        self.assertIn("read_ain(d, ETL_AIN)", src)
        self.assertIn("measure_power_mw(", src)
        self.assertIn("meters.get(\"P0005053\")", src)
        self.assertIn("Post-ETL powermeter", src)
        self.assertIn("open_power_logs(", src)
        self.assertIn("close_power_logs(", src)
        self.assertIn("default_logs_root(__file__)", src)
        self.assertNotIn("save_power_logs(", src)
        self.assertNotIn("stim_samples", src)

        input_pos = src.find('powermeter = input("Which powermeter')
        start_pos = src.find("start_experiment(")
        append_pos = src.find("append_power_sample(")
        self.assertGreater(input_pos, 0)
        self.assertGreater(start_pos, input_pos)
        self.assertGreater(append_pos, start_pos)


if __name__ == "__main__":
    unittest.main()
