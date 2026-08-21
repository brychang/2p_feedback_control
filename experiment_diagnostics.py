"""Load a feedback experiment folder, detect stims, and compute diagnostic stats."""

from __future__ import annotations

import os
import re

import numpy as np

from experiment_logging import default_logs_root, legacy_logs_root, power_log_filenames

FOLDER_NAME_RE = re.compile(r"^feedback_control_(\d{8}_\d{6})$")


def _read_float_column(path):
    if not path or not os.path.isfile(path):
        return None
    values = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            if text.lower() == "nan":
                values.append(np.nan)
            else:
                values.append(float(text))
    return np.asarray(values, dtype=float)


def has_finite_readings(series):
    """True if the series exists and contains at least one real number."""
    if series is None:
        return False
    values = np.asarray(series, dtype=float)
    return values.size > 0 and bool(np.any(np.isfinite(values)))


def find_latest_experiment(logs_root=None):
    """Return the newest feedback_control_YYYYMMDD_HHMMSS folder."""
    if logs_root is None:
        roots = [default_logs_root(), legacy_logs_root()]
    else:
        roots = [logs_root]
    dated = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            match = FOLDER_NAME_RE.match(name)
            if not match:
                continue
            path = os.path.join(root, name)
            if path in seen:
                continue
            seen.add(path)
            dated.append((match.group(1), path))
    if not dated:
        searched = ", ".join(roots)
        raise FileNotFoundError(f"No feedback_control_* folders in {searched}")
    dated.sort(key=lambda item: item[0])
    return dated[-1][1]


def _paths_for_folder(folder):
    name = os.path.basename(os.path.abspath(folder))
    match = FOLDER_NAME_RE.match(name)
    if match:
        names = power_log_filenames(match.group(1))
        return {key: os.path.join(folder, filename) for key, filename in names.items()}

    found = {}
    for key, prefix in (
        ("stim", "stim_power_log_"),
        ("feedback", "feedback_power_log_"),
        ("etl", "etl_voltage_log_"),
        ("shutter", "shutter_voltage_log_"),
        ("time", "sample_time_log_"),
    ):
        matches = [
            os.path.join(folder, filename)
            for filename in os.listdir(folder)
            if filename.startswith(prefix) and filename.endswith(".txt")
        ]
        if matches:
            found[key] = sorted(matches)[-1]
    return found


def load_experiment(folder):
    paths = _paths_for_folder(folder)
    stim = _read_float_column(paths.get("stim"))
    feedback = _read_float_column(paths.get("feedback"))
    if feedback is None or len(feedback) == 0:
        raise FileNotFoundError(f"Missing feedback power log in {folder}")
    if stim is None or len(stim) == 0:
        stim = np.full(len(feedback), np.nan)
    n = min(len(stim), len(feedback))
    stim = stim[:n]
    feedback = feedback[:n]

    def _aligned(key):
        series = _read_float_column(paths.get(key))
        if series is None or len(series) == 0:
            return None
        if len(series) >= n:
            return series[:n]
        padded = np.full(n, np.nan)
        padded[: len(series)] = series
        return padded

    times = _aligned("time")
    if times is None or not np.any(np.isfinite(times)):
        times = np.arange(n, dtype=float)
        time_is_sample_index = True
    else:
        time_is_sample_index = False

    etl = _aligned("etl")
    stim_ok = has_finite_readings(stim)
    etl_ok = has_finite_readings(etl)

    return {
        "folder": os.path.abspath(folder),
        "stim": stim,
        "feedback": feedback,
        "etl": etl,
        "shutter": _aligned("shutter"),
        "time": times,
        "time_is_sample_index": time_is_sample_index,
        "n": n,
        "has_stim": stim_ok,
        "has_etl": etl_ok,
    }


def otsu_threshold(values, n_bins=64):
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size < 8:
        return None
    hist, edges = np.histogram(finite, bins=n_bins)
    hist = hist.astype(float)
    if hist.sum() <= 0:
        return None
    centers = 0.5 * (edges[:-1] + edges[1:])
    omega = np.cumsum(hist / hist.sum())
    mu = np.cumsum(hist / hist.sum() * centers)
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    sigma_b = np.zeros_like(omega)
    np.divide((mu_t * omega - mu) ** 2, denom, out=sigma_b, where=denom > 0)
    idx = int(np.argmax(sigma_b))
    return float(centers[idx])


def _mask_to_events(mask):
    events = []
    start = None
    for i, flag in enumerate(mask):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            events.append((start, i))
            start = None
    if start is not None:
        events.append((start, len(mask)))
    return events


def _merge_short_gaps(events, times, min_gap_s):
    if not events:
        return events
    merged = [list(events[0])]
    for start, end in events[1:]:
        prev_end = merged[-1][1]
        gap = times[start] - times[prev_end - 1]
        if gap <= min_gap_s:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def detect_stim_events(data, min_gap_s=0.02, min_samples=2):
    """Detect stimulation windows from shutter voltage or pulsed stim power.

    Returns a dict with boolean mask, event index pairs, method name, and threshold.
    """
    n = data["n"]
    times = data["time"]
    shutter = data["shutter"]
    stim = data["stim"]

    method = "none"
    threshold = None
    mask = np.zeros(n, dtype=bool)

    if shutter is not None and np.any(np.isfinite(shutter)):
        shutter_finite = shutter[np.isfinite(shutter)]
        if shutter_finite.size and (np.nanmax(shutter) - np.nanmin(shutter)) > 0.15:
            threshold = max(0.1, 0.5 * (np.nanmin(shutter) + np.nanmax(shutter)))
            mask = np.isfinite(shutter) & (shutter > threshold)
            method = "shutter_voltage"

    if method == "none":
        finite = stim[np.isfinite(stim)]
        if finite.size:
            threshold = otsu_threshold(finite)
            if threshold is not None:
                high = finite[finite >= threshold]
                low = finite[finite < threshold]
                separated = (
                    high.size >= max(min_samples, 0.005 * finite.size)
                    and low.size >= 0.05 * finite.size
                    and np.median(high) > np.median(low) + max(0.02, 0.15 * (np.median(low) + 1e-12))
                )
                if separated:
                    mask = np.isfinite(stim) & (stim >= threshold)
                    method = "stim_power_otsu"

    events = _mask_to_events(mask)
    events = _merge_short_gaps(events, times, min_gap_s)
    events = [(start, end) for start, end in events if (end - start) >= min_samples]
    mask[:] = False
    for start, end in events:
        mask[start:end] = True

    event_records = []
    for start, end in events:
        event_records.append(
            {
                "start_index": start,
                "end_index": end,
                "t_start": float(times[start]),
                "t_end": float(times[end - 1]),
                "duration": float(times[end - 1] - times[start]),
                "stim_mean": float(np.nanmean(stim[start:end])),
                "feedback_mean": float(np.nanmean(data["feedback"][start:end])),
                "etl_mean": (
                    float(np.nanmean(data["etl"][start:end]))
                    if data["etl"] is not None
                    else float("nan")
                ),
            }
        )

    return {
        "method": method,
        "threshold": threshold,
        "mask": mask,
        "events": event_records,
        "n_events": len(event_records),
    }


def pearson_r(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x = x[valid]
    y = y[valid]
    if x.size < 3 or np.std(x) == 0 or np.std(y) == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def variance_stats(values):
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return {"n": 0, "mean": float("nan"), "std": float("nan"), "var": float("nan"), "cv": float("nan")}
    mean = float(np.mean(finite))
    std = float(np.std(finite, ddof=1) if finite.size > 1 else 0.0)
    var = float(std * std)
    cv = float(std / mean) if mean != 0 else float("nan")
    return {"n": int(finite.size), "mean": mean, "std": std, "var": var, "cv": cv}


def compute_diagnostics(data, detection=None):
    if detection is None:
        detection = detect_stim_events(data)
    mask = detection["mask"]
    stim = data["stim"]
    feedback = data["feedback"]
    etl = data["etl"]

    stim_at_events = stim[mask] if np.any(mask) else np.array([])
    feedback_at_events = feedback[mask] if np.any(mask) else np.array([])
    has_etl = has_finite_readings(etl)
    etl_at_events = etl[mask] if has_etl and np.any(mask) else np.array([])

    event_stim = np.array([ev["stim_mean"] for ev in detection["events"]], dtype=float)
    event_feedback = np.array([ev["feedback_mean"] for ev in detection["events"]], dtype=float)
    event_etl = np.array([ev["etl_mean"] for ev in detection["events"]], dtype=float)
    isis = np.diff([ev["t_start"] for ev in detection["events"]]) if detection["n_events"] >= 2 else np.array([])

    return {
        "detection": detection,
        "stim_all": variance_stats(stim),
        "feedback_all": variance_stats(feedback),
        "etl_all": variance_stats(etl) if has_etl else None,
        "has_etl": has_etl,
        "has_stim": has_finite_readings(stim),
        "stim_during_events": variance_stats(stim_at_events),
        "feedback_during_events": variance_stats(feedback_at_events),
        "r_stim_etl_all": pearson_r(stim, etl) if has_etl else float("nan"),
        "r_stim_etl_at_stim": pearson_r(stim_at_events, etl_at_events) if has_etl else float("nan"),
        "r_stim_feedback_at_stim": pearson_r(stim_at_events, feedback_at_events),
        "r_event_stim_etl": pearson_r(event_stim, event_etl) if has_etl else float("nan"),
        "r_event_stim_feedback": pearson_r(event_stim, event_feedback),
        "isi": isis,
        "isi_median": float(np.median(isis)) if isis.size else float("nan"),
    }


def format_diagnostics_text(data, diagnostics):
    detection = diagnostics["detection"]
    lines = [
        f"Experiment folder: {data['folder']}",
        f"Samples: {data['n']}",
        f"Time axis: {'sample index' if data['time_is_sample_index'] else 'logged seconds'}",
        "",
        f"Stim detection method: {detection['method']}",
        f"Threshold: {detection['threshold']}",
        f"Detected stim events: {detection['n_events']}",
        f"Median inter-stim interval: {diagnostics['isi_median']}",
        "",
        "Variance (all samples):",
        f"  stim     mean={diagnostics['stim_all']['mean']:.6g}  std={diagnostics['stim_all']['std']:.6g}  var={diagnostics['stim_all']['var']:.6g}  CV={diagnostics['stim_all']['cv']:.4g}",
        f"  feedback mean={diagnostics['feedback_all']['mean']:.6g}  std={diagnostics['feedback_all']['std']:.6g}  var={diagnostics['feedback_all']['var']:.6g}  CV={diagnostics['feedback_all']['cv']:.4g}",
    ]
    if diagnostics.get("etl_all") is not None:
        etl = diagnostics["etl_all"]
        lines.append(
            f"  ETL V    mean={etl['mean']:.6g}  std={etl['std']:.6g}  var={etl['var']:.6g}  CV={etl['cv']:.4g}"
        )
    else:
        lines.append("  ETL V    skipped (no ETL connected / no analog readings)")
    lines.extend(
        [
            "",
            "During detected stims:",
            f"  stim     mean={diagnostics['stim_during_events']['mean']:.6g}  std={diagnostics['stim_during_events']['std']:.6g}",
            f"  feedback mean={diagnostics['feedback_during_events']['mean']:.6g}  std={diagnostics['feedback_during_events']['std']:.6g}",
            "",
            "Correlations (Pearson r):",
        ]
    )
    if diagnostics.get("has_etl"):
        lines.extend(
            [
                f"  stim vs ETL, all samples: {diagnostics['r_stim_etl_all']:.4f}",
                f"  stim vs ETL, at stim samples: {diagnostics['r_stim_etl_at_stim']:.4f}",
                f"  stim vs ETL, per-event means: {diagnostics['r_event_stim_etl']:.4f}",
            ]
        )
    else:
        lines.append("  stim vs ETL: skipped (no ETL connected / no analog readings)")
    lines.extend(
        [
            f"  stim vs feedback, at stim samples: {diagnostics['r_stim_feedback_at_stim']:.4f}",
            f"  stim vs feedback, per-event means: {diagnostics['r_event_stim_feedback']:.4f}",
            "",
        ]
    )
    return "\n".join(lines)


def save_diagnostic_plots(data, diagnostics, output_dir):
    """Write each diagnostic as its own PNG.

    Returns (saved_paths, skipped_messages). ETL-only plots are skipped when
    there are no finite ETL analog readings.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(output_dir, exist_ok=True)
    saved = []
    skipped = []
    stim = data["stim"]
    feedback = data["feedback"]
    etl = data["etl"]
    times = data["time"]
    mask = diagnostics["detection"]["mask"]
    xlabel_time = "Sample" if data["time_is_sample_index"] else "Time (s)"
    has_stim = diagnostics.get("has_stim", has_finite_readings(stim))
    has_etl = diagnostics.get("has_etl", has_finite_readings(etl))

    def _save(fig, name):
        path = os.path.join(output_dir, name)
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)
        saved.append(path)

    if has_stim:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.hist(stim[np.isfinite(stim)], bins=50, color="tab:red", alpha=0.85)
        ax.set_xlabel("Stim power (mW)")
        ax.set_ylabel("Count")
        ax.set_title("Histogram of all stim-path power samples")
        _save(fig, "histogram_stim_power_all.png")

        fig, ax = plt.subplots(figsize=(7, 4.5))
        if np.any(mask):
            ax.hist(stim[mask], bins=40, color="tab:red", alpha=0.85)
            ax.set_title("Histogram of stim power during detected stims")
        else:
            ax.text(0.5, 0.5, "No pulsed stims detected", ha="center", va="center")
            ax.set_title("Histogram of stim power during detected stims")
        ax.set_xlabel("Stim power (mW)")
        ax.set_ylabel("Count")
        _save(fig, "histogram_stim_power_during_stims.png")

        fig, ax = plt.subplots(figsize=(7, 4.5))
        event_means = [ev["stim_mean"] for ev in diagnostics["detection"]["events"]]
        if event_means:
            ax.hist(event_means, bins=min(20, max(5, len(event_means))), color="tab:purple", alpha=0.85)
            ax.set_title("Histogram of mean stim power per detected event")
        else:
            ax.text(0.5, 0.5, "No stim events", ha="center", va="center")
            ax.set_title("Histogram of mean stim power per detected event")
        ax.set_xlabel("Event mean stim power (mW)")
        ax.set_ylabel("Events")
        _save(fig, "histogram_stim_event_means.png")

        fig, ax = plt.subplots(figsize=(9, 4.5))
        ax.plot(times, stim, color="tab:red", lw=0.8, label="stim power")
        if np.any(mask):
            ax.scatter(times[mask], stim[mask], s=8, color="black", label="detected stim")
        ax.set_xlabel(xlabel_time)
        ax.set_ylabel("Stim power (mW)")
        ax.set_title(
            f"Stim detection ({diagnostics['detection']['method']}, "
            f"{diagnostics['detection']['n_events']} events)"
        )
        ax.legend(loc="best")
        _save(fig, "stim_detection_timeline.png")
    else:
        skipped.append("stim-power histograms and timeline (no post-ETL / stim-path readings)")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.hist(feedback[np.isfinite(feedback)], bins=50, color="tab:blue", alpha=0.85)
    ax.set_xlabel("Feedback power (mW)")
    ax.set_ylabel("Count")
    ax.set_title("Histogram of feedback power")
    _save(fig, "histogram_feedback_power.png")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    labels = ["feedback std"]
    heights = [diagnostics["feedback_all"]["std"]]
    colors = ["tab:blue"]
    if has_stim:
        labels.insert(0, "stim std")
        heights.insert(0, diagnostics["stim_all"]["std"])
        colors.insert(0, "tab:red")
    if has_etl:
        labels.append("ETL V std")
        heights.append(diagnostics["etl_all"]["std"])
        colors.append("tab:green")
    ax.bar(labels, heights, color=colors)
    ax.set_ylabel("Standard deviation")
    ax.set_title(
        "Variance (std): "
        f"stim={diagnostics['stim_all']['std']:.4g}, "
        f"feedback={diagnostics['feedback_all']['std']:.4g}"
    )
    _save(fig, "variance_std_comparison.png")

    if has_etl:
        fig, ax = plt.subplots(figsize=(6.5, 6))
        ax.scatter(etl, stim, s=8, alpha=0.35, color="gray", label="all samples")
        if np.any(mask):
            ax.scatter(etl[mask], stim[mask], s=14, alpha=0.8, color="tab:red", label="at stim")
        ax.set_xlabel("ETL analog (V)")
        ax.set_ylabel("Stim power (mW)")
        ax.set_title(
            f"Stim vs ETL  r_all={diagnostics['r_stim_etl_all']:.3f}  "
            f"r_stim={diagnostics['r_stim_etl_at_stim']:.3f}"
        )
        ax.legend(loc="best")
        _save(fig, "correlation_stim_vs_etl.png")
    else:
        skipped.append("correlation_stim_vs_etl.png (no ETL connected / no analog readings)")

    if has_stim:
        fig, ax = plt.subplots(figsize=(6.5, 6))
        if np.any(mask):
            ax.scatter(feedback[mask], stim[mask], s=14, alpha=0.8, color="tab:blue")
            ax.set_title(
                f"Stim vs feedback at stim  r={diagnostics['r_stim_feedback_at_stim']:.3f}"
            )
        else:
            ax.text(0.5, 0.5, "No detected stims to correlate", ha="center", va="center")
            ax.set_title("Stim vs feedback at stim")
        ax.set_xlabel("Feedback power at stim (mW)")
        ax.set_ylabel("Stim power at stim (mW)")
        _save(fig, "correlation_stim_vs_feedback_at_stim.png")
    else:
        skipped.append("correlation_stim_vs_feedback_at_stim.png (no stim-path readings)")

    summary_path = os.path.join(output_dir, "diagnostics_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(format_diagnostics_text(data, diagnostics))
    saved.append(summary_path)
    return saved, skipped
