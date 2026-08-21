"""Compare hold-loop strategies on the live pre-MEMS powermeter.

The current loop jogs a fixed step whenever the 4 s average is outside the
deadband. If that step moves more power than the deadband, the motor overshoots
and the next cycle jogs the other way.

This script runs several alternatives for the same duration, with the same
target, tolerance, and large max step, then ranks them by:
  1) fraction of cycles inside the deadband
  2) RMS error
  3) direction reversals (hunting)

no_control is included so you can see laser wander with the motor frozen. If a
strategy scores worse than no_control, it is making the power less stable.

Usage (lab PC, laser on, pre-MEMS connected):

    python compare_feedback_strategies.py

Dry-run without hardware (simulated drifting laser):

    python compare_feedback_strategies.py --simulate

Press q to abort, or n to skip the rest of the current strategy.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from datetime import datetime

from feedback_strategies import (
    BACKWARD,
    FORWARD,
    HOLD,
    DIRECTION_NAMES,
    DriftingPowerPlant,
    aggregate_metrics,
    format_results_table,
    make_strategies,
    rank_strategies,
    run_hold_cycles,
    summarize_records,
)


def prompt_float(message, default=None):
    suffix = f" [{default}]" if default is not None else ""
    raw = input(f"{message}{suffix}: ").strip()
    if raw == "" and default is not None:
        return float(default)
    return float(raw)


def prompt_int(message, default=None):
    suffix = f" [{default}]" if default is not None else ""
    raw = input(f"{message}{suffix}: ").strip()
    if raw == "" and default is not None:
        return int(default)
    return int(raw)


def prompt_yes(message, default=True):
    hint = "Y/n" if default else "y/N"
    raw = input(f"{message} [{hint}]: ").strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare feedback hold-loop strategies on pre-MEMS."
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use a drifting simulated laser instead of hardware.",
    )
    parser.add_argument("--target", type=float, help="Target power (mW).")
    parser.add_argument("--tolerance", type=float, help="Deadband (mW).")
    parser.add_argument("--max-step", type=float, help="Large jog size to stress (deg).")
    parser.add_argument("--min-step", type=float, default=0.05, help="Smallest allowed jog (deg).")
    parser.add_argument("--seconds", type=float, default=120.0, help="Seconds per strategy.")
    parser.add_argument("--sample-seconds", type=float, default=4.0, help="Average window (s).")
    parser.add_argument("--rounds", type=int, default=1, help="Repeats (averages out laser drift).")
    parser.add_argument("--starting-degree", type=float, default=45.0)
    parser.add_argument("--cycles", type=int, default=80, help="Simulated cycles per strategy.")
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args(argv)


def collect_settings(args):
    settings = {
        "simulate": args.simulate,
        "min_step": args.min_step,
        "seconds": args.seconds,
        "sample_seconds": args.sample_seconds,
        "rounds": args.rounds,
        "starting_degree": args.starting_degree,
        "cycles": args.cycles,
        "ignore_shutter": True,
        "powermeter": "1",
    }
    interactive = not args.simulate and sys.stdin.isatty()
    if interactive and args.target is None:
        print("Live comparison on pre-MEMS. Laser should be on.")
        print("Use a step size large enough that the current loop was unstable.")
        settings["target"] = prompt_float("Enter target power (mW)")
        settings["tolerance"] = prompt_float("Enter tolerance (mW)")
        settings["max_step"] = prompt_float("Enter LARGE step size (deg)")
        settings["min_step"] = prompt_float("Enter minimum step size (deg)", args.min_step)
        settings["seconds"] = prompt_float("Seconds per strategy", args.seconds)
        settings["sample_seconds"] = prompt_float("Sample window (s)", args.sample_seconds)
        settings["rounds"] = prompt_int("Number of rounds", args.rounds)
        settings["starting_degree"] = prompt_float("Starting degree", args.starting_degree)
    else:
        settings["target"] = args.target if args.target is not None else 2.0
        settings["tolerance"] = args.tolerance if args.tolerance is not None else 0.1
        settings["max_step"] = args.max_step if args.max_step is not None else 0.5
    return settings


def key_command():
    try:
        import msvcrt
    except ImportError:
        return None
    if msvcrt.kbhit():
        key = msvcrt.getch()
        if key in (b"q", b"Q"):
            return "quit"
        if key in (b"n", b"N"):
            return "next"
    return None


def wait_until_idle(controller):
    while controller.IsDeviceBusy:
        time.sleep(0.05)


def set_jog_step(controller, Decimal, JogParametersBase, step_deg):
    jog_params = controller.GetJogParams()
    jog_params.StepSize = Decimal(float(step_deg))
    jog_params.MaxVelocity = Decimal(0.5)
    jog_params.JogMode = JogParametersBase.JogModes.SingleStep
    controller.SetJogParams(jog_params)


def motor_position(controller):
    try:
        return float(str(controller.Position))
    except Exception:
        return float("nan")


def write_csv(path, rows, fieldnames):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_summary(path, settings, ranked, extra_lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("Feedback strategy comparison\n")
        f.write(f"Timestamp: {settings['timestamp']}\n")
        f.write(f"Mode: {'simulate' if settings['simulate'] else 'live pre-MEMS'}\n")
        f.write(f"Target power (mW): {settings['target']}\n")
        f.write(f"Tolerance (mW): {settings['tolerance']}\n")
        f.write(f"Max step (deg): {settings['max_step']}\n")
        f.write(f"Min step (deg): {settings['min_step']}\n")
        if settings["simulate"]:
            f.write(f"Cycles per strategy: {settings['cycles']}\n")
        else:
            f.write(f"Seconds per strategy: {settings['seconds']}\n")
            f.write(f"Sample window (s): {settings['sample_seconds']}\n")
        f.write(f"Rounds: {settings['rounds']}\n")
        f.write("\n")
        f.write("Ranking (best first): time in band, then RMS error, then reversals.\n\n")
        f.write(format_results_table(ranked))
        f.write("\n\n")
        if ranked:
            best_name, best = ranked[0]
            f.write(f"BEST: {best_name}\n")
            f.write(
                f"  {100 * best['time_in_band_frac']:.1f}% of cycles inside "
                f"+/- {settings['tolerance']} mW, RMS error {best['rms_error']:.4f} mW, "
                f"{best['n_reversals']:.1f} reversals.\n"
            )
        f.write("\n")
        f.write("\n".join(extra_lines))
        f.write("\n")


def init_hardware(starting_degree):
    import clr
    import os as os_mod
    import u3
    from ctypes import byref, c_double, c_uint32, create_string_buffer

    os_mod.add_dll_directory(os_mod.getcwd())
    clr.AddReference(
        "C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.DeviceManagerCLI.dll"
    )
    clr.AddReference(
        "C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.GenericMotorCLI.dll"
    )
    clr.AddReference(
        "C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.KCube.DCServoCLI.dll"
    )
    from Thorlabs.MotionControl.DeviceManagerCLI import DeviceManagerCLI, DeviceConfiguration
    from Thorlabs.MotionControl.GenericMotorCLI import MotorDirection
    from Thorlabs.MotionControl.GenericMotorCLI.ControlParameters import JogParametersBase
    from Thorlabs.MotionControl.KCube.DCServoCLI import KCubeDCServo
    from System import Decimal
    from TLPMX import TLPMX

    finder = TLPMX()
    device_count = c_uint32()
    finder.findRsrc(byref(device_count))
    if device_count.value == 0:
        raise RuntimeError("No connected powermeter.")

    meters = {}
    for i in range(device_count.value):
        resource = create_string_buffer(1024)
        finder.getRsrcName(i, resource)
        meter = TLPMX()
        meter.open(resource, True, True)
        serial = resource.value.decode().split("::")[3]
        meters[serial] = meter

    preMEMS = meters["P0040956"]
    preMEMS.setWavelength(c_double(850.0), 1)
    current_power = c_double()
    preMEMS.measPower(byref(current_power), 1)
    print(f"Pre MEMS: {current_power.value:.6e} W")

    serial_num = "27273099"
    DeviceManagerCLI.BuildDeviceList()
    controller = KCubeDCServo.CreateKCubeDCServo(serial_num)
    if controller is None:
        raise RuntimeError("Motor controller not found.")
    controller.Connect(serial_num)
    if not controller.IsSettingsInitialized():
        controller.WaitForSettingsInitialized(3000)
    controller.StartPolling(50)
    time.sleep(0.1)
    controller.EnableDevice()
    time.sleep(0.1)
    config = controller.LoadMotorConfiguration(
        serial_num, DeviceConfiguration.DeviceSettingsUseOptionType.UseFileSettings
    )
    config.DeviceSettingsName = str("PRMI-Z8")
    config.UpdateCurrentConfiguration()
    controller.SetSettings(controller.MotorDeviceSettings, True, False)

    status_bits = controller.GetStatusBits()
    is_homed = (status_bits & 0x00000400) != 0
    if not is_homed:
        print("Device homing...")
        controller.Home(60000)
    else:
        print("Device already homed.")

    controller.MoveTo(Decimal(float(starting_degree)), 60000)
    print(f"Moved to {starting_degree} deg")

    labjack = None
    try:
        labjack = u3.U3()
    except Exception as exc:
        print(f"LabJack not opened ({exc}); shutter will be ignored.")

    hardware = {
        "preMEMS": preMEMS,
        "controller": controller,
        "labjack": labjack,
        "Decimal": Decimal,
        "JogParametersBase": JogParametersBase,
        "MotorDirection": MotorDirection,
        "c_double": c_double,
        "byref": byref,
        "meters": meters,
    }
    return hardware


def close_hardware(hardware):
    controller = hardware.get("controller")
    if controller is not None:
        try:
            controller.StopPolling()
            controller.Disconnect(False)
        except Exception:
            pass
    for meter in hardware.get("meters", {}).values():
        try:
            meter.close()
        except Exception:
            pass
    labjack = hardware.get("labjack")
    if labjack is not None:
        try:
            labjack.close()
        except Exception:
            pass


def acquire_with_small_steps(hardware, settings, stop_flag):
    """Lock near target with min_step before each strategy so hold tests start fairly."""
    controller = hardware["controller"]
    preMEMS = hardware["preMEMS"]
    Decimal = hardware["Decimal"]
    JogParametersBase = hardware["JogParametersBase"]
    MotorDirection = hardware["MotorDirection"]
    c_double = hardware["c_double"]
    byref = hardware["byref"]
    target = settings["target"]
    tolerance = max(settings["tolerance"], 0.025 * abs(target))
    current_power = c_double()
    set_jog_step(controller, Decimal, JogParametersBase, settings["min_step"])
    in_band = 0
    print("Acquiring with small steps...")
    deadline = time.time() + 90.0
    while time.time() < deadline:
        cmd = key_command()
        if cmd == "quit":
            stop_flag[0] = True
            return False
        if cmd == "next":
            return False
        preMEMS.measPower(byref(current_power), 1)
        power = current_power.value * 1000.0
        wait_until_idle(controller)
        if abs(power - target) <= tolerance:
            in_band += 1
            if in_band >= 5:
                print(f"Acquired at {power:.4f} mW")
                return True
        else:
            in_band = 0
            if power > target:
                controller.MoveJog(MotorDirection.Backward, 0)
            else:
                controller.MoveJog(MotorDirection.Forward, 0)
        time.sleep(0.15)
    print("Acquisition timed out; starting strategy from current power.")
    return True


def measure_average_live(hardware, settings, stop_flag):
    preMEMS = hardware["preMEMS"]
    c_double = hardware["c_double"]
    byref = hardware["byref"]
    current_power = c_double()
    samples = []
    start = time.time()
    while time.time() - start < settings["sample_seconds"]:
        cmd = key_command()
        if cmd == "quit":
            stop_flag[0] = True
            break
        if cmd == "next":
            stop_flag[1] = True
            break
        preMEMS.measPower(byref(current_power), 1)
        samples.append(current_power.value * 1000.0)
    if not samples:
        return None
    return sum(samples) / len(samples)


def jog_live(hardware, direction, step_deg):
    controller = hardware["controller"]
    Decimal = hardware["Decimal"]
    JogParametersBase = hardware["JogParametersBase"]
    MotorDirection = hardware["MotorDirection"]
    wait_until_idle(controller)
    set_jog_step(controller, Decimal, JogParametersBase, step_deg)
    if direction == FORWARD:
        controller.MoveJog(MotorDirection.Forward, 0)
    elif direction == BACKWARD:
        controller.MoveJog(MotorDirection.Backward, 0)
    wait_until_idle(controller)


def run_live_strategy(strategy, hardware, settings, stop_flag):
    records = []
    t0 = time.time()
    cycle = 0
    while time.time() - t0 < settings["seconds"]:
        if stop_flag[0] or stop_flag[1]:
            break
        avg_power = measure_average_live(hardware, settings, stop_flag)
        if avg_power is None or stop_flag[0] or stop_flag[1]:
            break
        direction, step_deg = strategy.decide(
            avg_power, settings["target"], settings["tolerance"]
        )
        if direction != HOLD:
            jog_live(hardware, direction, step_deg)
            action = DIRECTION_NAMES[direction]
            print(
                f"  t={time.time() - t0:6.1f}s  avg={avg_power:.4f} mW  "
                f"{action} {step_deg:.3f} deg"
            )
        else:
            print(f"  t={time.time() - t0:6.1f}s  avg={avg_power:.4f} mW  hold")
        records.append(
            {
                "cycle": cycle,
                "t_s": time.time() - t0,
                "avg_power": avg_power,
                "error": avg_power - settings["target"],
                "in_band": int(abs(avg_power - settings["target"]) <= settings["tolerance"]),
                "action": DIRECTION_NAMES[direction],
                "direction": direction,
                "step_deg": step_deg,
                "position_deg": motor_position(hardware["controller"]),
            }
        )
        cycle += 1
    return records


def run_simulated_comparison(settings, output_dir):
    extra_lines = [
        "Simulated plant: power += direction * step * 1.0 mW/deg, plus random-walk drift.",
        "This is a dry-run. Live ranking can differ because the real polarizer curve is not linear.",
    ]
    named_rows = {}
    all_metrics = []
    for round_idx in range(settings["rounds"]):
        seed = 1000 + round_idx
        for strategy in make_strategies(settings["max_step"], settings["min_step"]):
            plant = DriftingPowerPlant(
                power_mw=settings["target"],
                mw_per_deg=1.0,
                drift_std=0.03,
                noise_std=0.01,
                rng=random.Random(seed),
            )
            records = run_hold_cycles(
                strategy,
                plant,
                settings["target"],
                settings["tolerance"],
                settings["cycles"],
            )
            metrics = summarize_records(records, settings["target"], settings["tolerance"])
            metrics["strategy"] = strategy.name
            metrics["round"] = round_idx + 1
            all_metrics.append(metrics)
            named_rows.setdefault(strategy.name, []).append(metrics)
            fieldnames = [
                "cycle",
                "avg_power",
                "direction",
                "step_deg",
                "position_deg",
            ]
            write_csv(
                os.path.join(output_dir, f"timeseries_{strategy.name}_r{round_idx + 1}.csv"),
                records,
                fieldnames,
            )
            print(
                f"Round {round_idx + 1} {strategy.name}: "
                f"in-band {100 * metrics['time_in_band_frac']:.1f}%, "
                f"RMS {metrics['rms_error']:.4f}, reversals {metrics['n_reversals']}"
            )
    return named_rows, all_metrics, extra_lines


def run_live_comparison(settings, output_dir, stop_flag):
    hardware = init_hardware(settings["starting_degree"])
    extra_lines = [
        "Live pre-MEMS run. Each strategy starts after a small-step acquire so the",
        "comparison is about hold stability, not who lucked into the right starting power.",
        "Strategy order is randomized each round to reduce order bias.",
    ]
    named_rows = {}
    all_metrics = []
    try:
        for round_idx in range(settings["rounds"]):
            if stop_flag[0]:
                break
            strategies = list(make_strategies(settings["max_step"], settings["min_step"]))
            random.shuffle(strategies)
            for strategy in strategies:
                if stop_flag[0]:
                    break
                stop_flag[1] = False
                print("\n" + "=" * 72)
                print(f"Round {round_idx + 1}  strategy: {strategy.name}")
                print(strategy.description)
                print("=" * 72)
                acquire_with_small_steps(hardware, settings, stop_flag)
                if stop_flag[0]:
                    break
                strategy.reset()
                records = run_live_strategy(strategy, hardware, settings, stop_flag)
                stop_flag[1] = False
                metrics = summarize_records(
                    records, settings["target"], settings["tolerance"]
                )
                metrics["strategy"] = strategy.name
                metrics["round"] = round_idx + 1
                all_metrics.append(metrics)
                named_rows.setdefault(strategy.name, []).append(metrics)
                out_rows = []
                for row in records:
                    out_rows.append(
                        {
                            "cycle": row["cycle"],
                            "t_s": f"{row['t_s']:.3f}",
                            "avg_power": f"{row['avg_power']:.6f}",
                            "error": f"{row['error']:.6f}",
                            "in_band": row["in_band"],
                            "action": row["action"],
                            "step_deg": f"{row['step_deg']:.4f}",
                            "position_deg": f"{row['position_deg']:.4f}",
                        }
                    )
                write_csv(
                    os.path.join(
                        output_dir, f"timeseries_{strategy.name}_r{round_idx + 1}.csv"
                    ),
                    out_rows,
                    [
                        "cycle",
                        "t_s",
                        "avg_power",
                        "error",
                        "in_band",
                        "action",
                        "step_deg",
                        "position_deg",
                    ],
                )
                print(
                    f"Finished {strategy.name}: in-band {100 * metrics['time_in_band_frac']:.1f}%, "
                    f"RMS {metrics['rms_error']:.4f} mW, reversals {metrics['n_reversals']}"
                )
    finally:
        close_hardware(hardware)
    return named_rows, all_metrics, extra_lines


def finish_and_report(settings, output_dir, named_rows, all_metrics, extra_lines):
    averaged = {
        name: aggregate_metrics(rows) for name, rows in named_rows.items() if rows
    }
    ranked = rank_strategies(averaged)
    table = format_results_table(ranked)
    print("\n" + table)
    if ranked:
        print(f"\nBEST: {ranked[0][0]}")
        print(
            "If BEST is no_control, the large step is hurting stability; prefer a "
            "strategy that beats no_control on in-band % and RMS."
        )
        print("adaptive is the proposed replacement if it ranks first or second.")

    metric_fields = [
        "strategy",
        "round",
        "n_cycles",
        "time_in_band_frac",
        "rms_error",
        "mean_abs_error",
        "max_abs_error",
        "n_reversals",
        "n_moves",
        "mean_step_when_moving",
    ]
    write_csv(os.path.join(output_dir, "metrics.csv"), all_metrics, metric_fields)
    write_summary(os.path.join(output_dir, "summary.txt"), settings, ranked, extra_lines)
    print(f"\nWrote results to {output_dir}")
    return ranked


def main(argv=None):
    args = parse_args(argv)
    settings = collect_settings(args)
    settings["timestamp"] = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "power_logs_experiments",
        f"strategy_compare_{settings['timestamp']}",
    )
    os.makedirs(output_dir, exist_ok=True)
    settings["output_dir"] = output_dir

    strategies = make_strategies(settings["max_step"], settings["min_step"])
    print("\nStrategies:")
    for item in strategies:
        print(f"  - {item.name}: {item.description}")
    n = len(strategies) * settings["rounds"]
    if settings["simulate"]:
        print(f"\nSimulate {n} runs x {settings['cycles']} cycles.")
    else:
        est_min = n * (settings["seconds"] + 30) / 60.0
        print(f"\nAbout {est_min:.0f} minutes. Press q to abort, n to skip a strategy.")

    stop_flag = [False, False]
    if settings["simulate"]:
        named_rows, all_metrics, extra_lines = run_simulated_comparison(
            settings, output_dir
        )
    else:
        named_rows, all_metrics, extra_lines = run_live_comparison(
            settings, output_dir, stop_flag
        )
    return finish_and_report(settings, output_dir, named_rows, all_metrics, extra_lines)


if __name__ == "__main__":
    main()
