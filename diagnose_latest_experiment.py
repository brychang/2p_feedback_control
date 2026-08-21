"""Plot diagnostics for the most recent feedback-control experiment folder.

Run this in a second VS Code terminal while an experiment is finished (or after q):

    python diagnose_latest_experiment.py

It finds the newest power_logs_experiments/feedback_control_* folder and writes
each plot as a separate PNG in that folder's diagnostics/ subfolder.

Optional:

    python diagnose_latest_experiment.py --folder path/to/feedback_control_...
"""

from __future__ import annotations

import argparse
import os
import sys

from experiment_diagnostics import (
    compute_diagnostics,
    find_latest_experiment,
    load_experiment,
    save_diagnostic_plots,
)
from experiment_logging import default_logs_root


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Make diagnostic plots from the latest feedback experiment logs."
    )
    parser.add_argument(
        "--folder",
        default=None,
        help="Experiment folder. Default: newest feedback_control_* under power_logs_experiments.",
    )
    parser.add_argument(
        "--logs-root",
        default=None,
        help="Override power_logs_experiments directory.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logs_root = args.logs_root or default_logs_root()
    folder = args.folder or find_latest_experiment(logs_root)
    print(f"Using experiment folder:\n  {folder}")

    data = load_experiment(folder)
    if not data.get("has_etl"):
        print("No ETL analog readings in this folder. Skipping plots that need ETL.")
    if not data.get("has_stim"):
        print("No stim-path / post-ETL power readings in this folder. Skipping stim plots.")

    diagnostics = compute_diagnostics(data)
    output_dir = os.path.join(folder, "diagnostics")
    saved, skipped = save_diagnostic_plots(data, diagnostics, output_dir)

    print()
    print(open(os.path.join(output_dir, "diagnostics_summary.txt"), encoding="utf-8").read())
    if skipped:
        print("Skipped:")
        for item in skipped:
            print(f"  {item}")
    print("Wrote:")
    for path in saved:
        print(f"  {path}")
    return saved


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
