"""Hold-loop strategies for rotary power control.

The current controller is fixed-step bang-bang: if the 4 s average is outside
the deadband, it always jogs the same user-chosen step. When that step moves
more power than the deadband, the loop overshoots and chatters.

The laser also wanders on its own, so a good strategy must:
- take smaller steps near the target (avoid hunting)
- still take larger steps when the laser jumps
- shrink the step after a direction reversal (overshoot)
- not chase single-sample glitches
"""

from __future__ import annotations

import math
import random

HOLD = 0
FORWARD = 1  # same convention as the live controller: increases power
BACKWARD = -1  # decreases power

DIRECTION_NAMES = {
    HOLD: "hold",
    FORWARD: "forward",
    BACKWARD: "backward",
}


def error_direction(avg_power, target_power):
    if avg_power > target_power:
        return BACKWARD
    if avg_power < target_power:
        return FORWARD
    return HOLD


def clip(value, lo, hi):
    return max(lo, min(hi, value))


class Strategy:
    name = "base"
    description = ""

    def reset(self):
        return None

    def decide(self, avg_power, target_power, tolerance):
        raise NotImplementedError


class NoControlStrategy(Strategy):
    """Reference: laser drift with the motor frozen."""

    name = "no_control"
    description = "Never move. Shows how much the laser wanders on its own."

    def decide(self, avg_power, target_power, tolerance):
        return HOLD, 0.0


class FixedStepStrategy(Strategy):
    """Current controller: fixed jog whenever the average is outside the band."""

    name = "fixed_step"
    description = "Baseline bang-bang with a constant step size."

    def __init__(self, step_deg):
        self.step_deg = float(step_deg)

    def decide(self, avg_power, target_power, tolerance):
        if abs(avg_power - target_power) <= tolerance:
            return HOLD, 0.0
        return error_direction(avg_power, target_power), self.step_deg


class ProportionalStrategy(Strategy):
    """Step scales with |error|. Large laser jumps get large moves; near-target stays fine."""

    name = "proportional"
    description = "Jog size is proportional to how far the average is from target."

    def __init__(self, max_step_deg, min_step_deg, error_for_max_steps=4.0):
        self.max_step_deg = float(max_step_deg)
        self.min_step_deg = float(min_step_deg)
        self.error_for_max_steps = float(error_for_max_steps)

    def _step_for_error(self, abs_error, tolerance):
        scale = self.error_for_max_steps * max(tolerance, 1e-9)
        return clip(self.max_step_deg * (abs_error / scale), self.min_step_deg, self.max_step_deg)

    def decide(self, avg_power, target_power, tolerance):
        error = avg_power - target_power
        if abs(error) <= tolerance:
            return HOLD, 0.0
        return error_direction(avg_power, target_power), self._step_for_error(abs(error), tolerance)


class ReversalDampingStrategy(Strategy):
    """Halve the step after a direction change; grow it again if error stays on one side."""

    name = "reversal_damping"
    description = "Shrinks the step when the loop overshoots; grows it if the laser keeps drifting."

    def __init__(self, max_step_deg, min_step_deg, shrink=0.5, grow=1.25, grow_after=3):
        self.max_step_deg = float(max_step_deg)
        self.min_step_deg = float(min_step_deg)
        self.shrink = float(shrink)
        self.grow = float(grow)
        self.grow_after = int(grow_after)
        self.step_deg = self.max_step_deg
        self.last_direction = HOLD
        self.same_dir_count = 0

    def reset(self):
        self.step_deg = self.max_step_deg
        self.last_direction = HOLD
        self.same_dir_count = 0

    def decide(self, avg_power, target_power, tolerance):
        if abs(avg_power - target_power) <= tolerance:
            self.last_direction = HOLD
            self.same_dir_count = 0
            self.step_deg = clip(self.step_deg * 1.05, self.min_step_deg, self.max_step_deg)
            return HOLD, 0.0

        direction = error_direction(avg_power, target_power)
        if self.last_direction not in (HOLD, 0) and direction != self.last_direction:
            self.step_deg = clip(self.step_deg * self.shrink, self.min_step_deg, self.max_step_deg)
            self.same_dir_count = 1
        else:
            self.same_dir_count += 1
            if self.same_dir_count >= self.grow_after:
                self.step_deg = clip(self.step_deg * self.grow, self.min_step_deg, self.max_step_deg)
                self.same_dir_count = 0
        self.last_direction = direction
        return direction, self.step_deg


class AdaptiveStrategy(ProportionalStrategy):
    """Proposed default: proportional step, then cut it in half on a reversal."""

    name = "adaptive"
    description = "Proportional step plus reversal damping. Candidate to replace fixed-step hold."

    def __init__(self, max_step_deg, min_step_deg, error_for_max_steps=4.0):
        super().__init__(max_step_deg, min_step_deg, error_for_max_steps)
        self.last_direction = HOLD
        self.last_step = max_step_deg

    def reset(self):
        self.last_direction = HOLD
        self.last_step = self.max_step_deg

    def decide(self, avg_power, target_power, tolerance):
        error = avg_power - target_power
        if abs(error) <= tolerance:
            self.last_direction = HOLD
            return HOLD, 0.0

        direction = error_direction(avg_power, target_power)
        step = self._step_for_error(abs(error), tolerance)
        if self.last_direction not in (HOLD, 0) and direction != self.last_direction:
            step = max(self.min_step_deg, min(step, self.last_step * 0.5))
        self.last_direction = direction
        self.last_step = step
        return direction, step


class ConfirmThenMoveStrategy(Strategy):
    """Ignore one-off glitches: only jog after two consecutive averages on the same side."""

    name = "confirm_then_move"
    description = "Waits for two same-sign errors before jogging. Rejects brief laser spikes."

    def __init__(self, step_deg, confirms=2):
        self.step_deg = float(step_deg)
        self.confirms = int(confirms)
        self.pending_direction = HOLD
        self.pending_count = 0

    def reset(self):
        self.pending_direction = HOLD
        self.pending_count = 0

    def decide(self, avg_power, target_power, tolerance):
        if abs(avg_power - target_power) <= tolerance:
            self.pending_direction = HOLD
            self.pending_count = 0
            return HOLD, 0.0

        direction = error_direction(avg_power, target_power)
        if direction == self.pending_direction:
            self.pending_count += 1
        else:
            self.pending_direction = direction
            self.pending_count = 1
        if self.pending_count >= self.confirms:
            return direction, self.step_deg
        return HOLD, 0.0


def make_strategies(max_step_deg, min_step_deg):
    """Strategies compared on the live laser, in display order."""
    return [
        NoControlStrategy(),
        FixedStepStrategy(max_step_deg),
        ProportionalStrategy(max_step_deg, min_step_deg),
        ReversalDampingStrategy(max_step_deg, min_step_deg),
        AdaptiveStrategy(max_step_deg, min_step_deg),
        ConfirmThenMoveStrategy(max_step_deg),
    ]


def summarize_records(records, target_power, tolerance):
    if not records:
        return {
            "n_cycles": 0,
            "time_in_band_frac": 0.0,
            "rms_error": float("nan"),
            "mean_abs_error": float("nan"),
            "max_abs_error": float("nan"),
            "n_reversals": 0,
            "n_moves": 0,
            "mean_step_when_moving": 0.0,
        }

    # If raw samples are recorded per cycle, compute metrics over the concatenated
    # sample stream so time-in-band reflects high-rate readings instead of one
    # averaged value per cycle.
    has_samples = any("samples" in row and row["samples"] for row in records)
    if has_samples:
        all_samples = []
        for row in records:
            all_samples.extend(row.get("samples") or [])
        n = len(all_samples)
        in_band = sum(1 for s in all_samples if abs(s - target_power) <= tolerance)
        errs = [s - target_power for s in all_samples]
        rms = math.sqrt(sum(e * e for e in errs) / n) if n else float("nan")
        mae = sum(abs(e) for e in errs) / n if n else float("nan")
        max_abs = max(abs(e) for e in errs) if n else float("nan")
    else:
        errors = [row["avg_power"] - target_power for row in records]
        n = len(errors)
        in_band = sum(1 for err in errors if abs(err) <= tolerance)
        rms = math.sqrt(sum(err * err for err in errors) / n)
        mae = sum(abs(err) for err in errors) / n
        max_abs = max(abs(err) for err in errors)

    n_reversals = 0
    n_moves = 0
    last_move_dir = HOLD
    moving_steps = []
    for row in records:
        direction = row["direction"]
        if direction == HOLD:
            continue
        n_moves += 1
        moving_steps.append(row["step_deg"])
        if last_move_dir != HOLD and direction != last_move_dir:
            n_reversals += 1
        last_move_dir = direction

    return {
        "n_cycles": len(records),
        "time_in_band_frac": in_band / (n if n else 1),
        "rms_error": rms,
        "mean_abs_error": mae,
        "max_abs_error": max_abs,
        "n_reversals": n_reversals,
        "n_moves": n_moves,
        "mean_step_when_moving": (sum(moving_steps) / len(moving_steps)) if moving_steps else 0.0,
    }


def aggregate_metrics(metric_rows):
    """Average several rounds of the same strategy."""
    if not metric_rows:
        return summarize_records([], 0.0, 0.0)
    keys = [
        "n_cycles",
        "time_in_band_frac",
        "rms_error",
        "mean_abs_error",
        "max_abs_error",
        "n_reversals",
        "n_moves",
        "mean_step_when_moving",
    ]
    out = {}
    for key in keys:
        out[key] = sum(row[key] for row in metric_rows) / len(metric_rows)
    return out


def rank_strategies(named_metrics):
    """Lower tuple is better: maximize time in band, then minimize RMS and reversals."""

    def sort_key(item):
        name, metrics = item
        return (
            -metrics["time_in_band_frac"],
            metrics["rms_error"],
            metrics["n_reversals"],
            metrics["mean_abs_error"],
            name,
        )

    return sorted(named_metrics.items(), key=sort_key)


def format_results_table(ranked):
    header = (
        f"{'Rank':<6}{'Strategy':<22}{'In-band %':>10}{'RMS err':>10}"
        f"{'MAE':>10}{'Max|err|':>10}{'Reversals':>11}{'Moves':>8}"
    )
    lines = [header, "-" * len(header)]
    for rank, (name, metrics) in enumerate(ranked, start=1):
        lines.append(
            f"{rank:<6}{name:<22}{100 * metrics['time_in_band_frac']:>10.1f}"
            f"{metrics['rms_error']:>10.4f}{metrics['mean_abs_error']:>10.4f}"
            f"{metrics['max_abs_error']:>10.4f}{metrics['n_reversals']:>11.1f}"
            f"{metrics['n_moves']:>8.1f}"
        )
    return "\n".join(lines)


class DriftingPowerPlant:
    """Simple plant for dry-runs and unit tests.

    Each degree of jog changes power by mw_per_deg, plus random-walk laser drift
    and measurement noise. A large step with a small deadband will overshoot.
    """

    def __init__(
        self,
        power_mw,
        mw_per_deg=1.0,
        drift_std=0.02,
        noise_std=0.01,
        rng=None,
    ):
        self.power_mw = float(power_mw)
        self.mw_per_deg = float(mw_per_deg)
        self.drift_std = float(drift_std)
        self.noise_std = float(noise_std)
        self.rng = rng or random.Random(0)
        self.position_deg = 0.0

    def measure(self):
        self.power_mw = max(0.0, self.power_mw + self.rng.gauss(0.0, self.drift_std))
        return max(0.0, self.power_mw + self.rng.gauss(0.0, self.noise_std))

    def jog(self, direction, step_deg):
        self.position_deg += direction * step_deg
        self.power_mw = max(0.0, self.power_mw + direction * step_deg * self.mw_per_deg)


def run_hold_cycles(strategy, plant, target_power, tolerance, n_cycles):
    """Run n decide/jog cycles against a plant. Used by tests and --simulate."""
    strategy.reset()
    records = []
    for i in range(n_cycles):
        avg_power = plant.measure()
        direction, step_deg = strategy.decide(avg_power, target_power, tolerance)
        if direction != HOLD:
            plant.jog(direction, step_deg)
        records.append(
            {
                "cycle": i,
                "avg_power": avg_power,
                "direction": direction,
                "step_deg": step_deg,
                "position_deg": plant.position_deg,
            }
        )
    return records
