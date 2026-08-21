"""Calibrate power change per motor degree and optionally apply correction.

Usage:
  python calibrate_power_to_position.py --delta 0.1 --sample-seconds 4 --apply

This script initializes hardware (like compare_feedback_strategies), measures the
power at current position and after small steps, computes mw_per_deg, and optionally
moves the motor to reduce the steady-state error to target.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime

from compare_feedback_strategies import (
    init_hardware,
    close_hardware,
    measure_average_live,
    wait_until_idle,
    motor_position,
)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Calibrate mW/deg for polarizer motor")
    p.add_argument("--delta", type=float, default=0.1, help="step size in deg for calibration")
    p.add_argument("--sample-seconds", type=float, default=4.0, help="averaging window (s)")
    p.add_argument("--starting-degree", type=float, default=45.0)
    p.add_argument("--apply", action="store_true", help="Apply correction to reach target (moves motor)")
    p.add_argument("--target", type=float, help="Optional target power (mW) to correct to")
    return p.parse_args(argv)


def write_rows(path, rows, fieldnames):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main(argv=None):
    args = parse_args(argv)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "power_logs_experiments", f"calibrate_{timestamp}")
    os.makedirs(output_dir, exist_ok=True)

    hardware = init_hardware(args.starting_degree)
    try:
        controller = hardware["controller"]
        preMEMS = hardware["preMEMS"]
        Decimal = hardware["Decimal"]
        JogParametersBase = hardware["JogParametersBase"]
        c_double = hardware["c_double"]
        byref = hardware["byref"]

        # measure baseline
        print("Measuring baseline...")
        baseline = measure_average_live(hardware, {"sample_seconds": args.sample_seconds, "tolerance": 0.0}, [False, False])
        pos0 = motor_position(controller)
        print(f"Position {pos0:.4f} deg -> power {baseline:.6f} mW")

        # move +delta
        from compare_feedback_strategies import Decimal as _Decimal
        print(f"Moving +{args.delta} deg for calibration")
        controller.MoveTo(_Decimal(float(pos0 + args.delta)), 60000)
        wait_until_idle(controller)
        time.sleep(0.2)
        plus = measure_average_live(hardware, {"sample_seconds": args.sample_seconds, "tolerance": 0.0}, [False, False])
        pos_plus = motor_position(controller)
        print(f"Position {pos_plus:.4f} deg -> power {plus:.6f} mW")

        # move -2*delta (to pos0 - delta)
        print(f"Moving -{2*args.delta} deg for calibration")
        controller.MoveTo(_Decimal(float(pos0 - args.delta)), 60000)
        wait_until_idle(controller)
        time.sleep(0.2)
        minus = measure_average_live(hardware, {"sample_seconds": args.sample_seconds, "tolerance": 0.0}, [False, False])
        pos_minus = motor_position(controller)
        print(f"Position {pos_minus:.4f} deg -> power {minus:.6f} mW")

        # return to original position
        print(f"Returning to original position {pos0:.4f} deg")
        controller.MoveTo(_Decimal(float(pos0)), 60000)
        wait_until_idle(controller)

        # compute slope
        dp = (plus - minus) / (pos_plus - pos_minus) if (pos_plus - pos_minus) != 0 else float('nan')
        print(f"Estimated slope: {dp:.6f} mW/deg")

        rows = [
            {"position_deg": pos_minus, "power_mw": minus},
            {"position_deg": pos0, "power_mw": baseline},
            {"position_deg": pos_plus, "power_mw": plus},
        ]
        write_rows(os.path.join(output_dir, "calibration_points.csv"), rows, ["position_deg", "power_mw"]) 

        # If requested, compute correction and move
        if args.apply and args.target is not None and not (dp != dp):
            measured = baseline
            needed_deg = (args.target - measured) / dp
            print(f"To reach target {args.target:.4f} mW need delta {needed_deg:.4f} deg")
            if abs(needed_deg) > 2.0:
                confirm = input(f"Move {needed_deg:.4f} deg (>2 deg). Proceed? [y/N]: ").strip().lower()
                if confirm not in ("y", "yes"):
                    print("Aborted moving.")
                else:
                    controller.MoveTo(_Decimal(float(pos0 + needed_deg)), 60000)
                    wait_until_idle(controller)
                    time.sleep(0.2)
                    new_power = measure_average_live(hardware, {"sample_seconds": args.sample_seconds, "tolerance": 0.0}, [False, False])
                    print(f"New position {motor_position(controller):.4f} deg -> power {new_power:.6f} mW")
            else:
                controller.MoveTo(_Decimal(float(pos0 + needed_deg)), 60000)
                wait_until_idle(controller)
                time.sleep(0.2)
                new_power = measure_average_live(hardware, {"sample_seconds": args.sample_seconds, "tolerance": 0.0}, [False, False])
                print(f"New position {motor_position(controller):.4f} deg -> power {new_power:.6f} mW")
                write_rows(os.path.join(output_dir, "post_move.csv"), [{"position_deg": motor_position(controller), "power_mw": new_power}], ["position_deg", "power_mw"]) 

        print(f"Calibration data written to {output_dir}")

    finally:
        close_hardware(hardware)


if __name__ == "__main__":
    main()
