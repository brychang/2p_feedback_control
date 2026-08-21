"""Helpers for timestamped feedback-control experiment folders and logs."""

import os
from datetime import datetime

POWER_LOGS_ROOT_NAME = "power_logs_experiments"
POWERMETER_LABELS = {
    "1": "1 (pre-MEMS)",
    "2": "2 (post-ETL)",
}

# LabJack analog channels on the feedback PC
SHUTTER_AIN = 0
ETL_AIN = 1


def format_experiment_timestamp(when=None):
    """Return a timestamp like 20260821_180521."""
    if when is None:
        when = datetime.now()
    return when.strftime("%Y%m%d_%H%M%S")


def experiment_folder_name(timestamp):
    return f"feedback_control_{timestamp}"


def default_logs_root(script_file=None):
    """Return power_logs_experiments next to the git repo, not inside it.

    Scripts live in the repo; logs go in ../power_logs_experiments/.
    """
    if script_file is None:
        script_file = __file__
    script_dir = os.path.dirname(os.path.abspath(script_file))
    return os.path.join(os.path.dirname(script_dir), POWER_LOGS_ROOT_NAME)


def legacy_logs_root(script_file=None):
    """Old in-repo log folder, used only to find experiments written before the move."""
    if script_file is None:
        script_file = __file__
    return os.path.join(os.path.dirname(os.path.abspath(script_file)), POWER_LOGS_ROOT_NAME)


def power_log_filenames(timestamp):
    return {
        "stim": f"stim_power_log_{timestamp}.txt",
        "feedback": f"feedback_power_log_{timestamp}.txt",
        "etl": f"etl_voltage_log_{timestamp}.txt",
        "shutter": f"shutter_voltage_log_{timestamp}.txt",
        "time": f"sample_time_log_{timestamp}.txt",
    }


def _powermeter_label(powermeter):
    key = str(powermeter).strip()
    return POWERMETER_LABELS.get(key, key)


def format_logbook(params):
    """Build logbook.txt contents from feedback-loop parameters."""
    timestamp = params["timestamp"]
    powermeter_label = _powermeter_label(params["powermeter"])
    lines = [
        "Feedback Control Experiment Logbook",
        f"Timestamp: {timestamp}",
        "",
        "User-entered parameters:",
        f"Target power (mW): {params['target_power']}",
        f"Tolerance (mW): {params['feedback_tolerance']}",
        f"Step size (deg): {params['degrees_to_move']}",
        f"Powermeter: {powermeter_label}",
        "",
        "Additional parameters:",
        f"Sampling average (s): {params['sample_seconds']}",
        f"Starting degree: {params['starting_degree']}",
        f"Initial tolerance (mW): {params['initial_tolerance']}",
        f"Testing state: {params['testing_state']}",
        f"ETL analog channel: LabJack AIN{params.get('etl_ain', ETL_AIN)}",
        f"Shutter analog channel: LabJack AIN{params.get('shutter_ain', SHUTTER_AIN)}",
        "",
    ]
    return "\n".join(lines)


def write_logbook(experiment_dir, params):
    path = os.path.join(experiment_dir, "logbook.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(format_logbook(params))
    return path


def start_experiment(logs_root, params, when=None, timestamp=None):
    """Create power_logs_experiments/feedback_control_<timestamp>/ and empty log files.

    Call this after the four user inputs have been collected.
    """
    timestamp = timestamp or format_experiment_timestamp(when)
    params = dict(params)
    params["timestamp"] = timestamp
    if "initial_tolerance" not in params:
        params["initial_tolerance"] = 0.025 * float(params["target_power"])
    if "testing_state" not in params:
        params["testing_state"] = False
    params.setdefault("etl_ain", ETL_AIN)
    params.setdefault("shutter_ain", SHUTTER_AIN)

    experiment_dir = os.path.join(logs_root, experiment_folder_name(timestamp))
    os.makedirs(experiment_dir, exist_ok=True)
    logbook_path = write_logbook(experiment_dir, params)
    names = power_log_filenames(timestamp)
    paths = {key: os.path.join(experiment_dir, filename) for key, filename in names.items()}
    for path in paths.values():
        with open(path, "w", encoding="utf-8"):
            pass

    return {
        "timestamp": timestamp,
        "experiment_dir": experiment_dir,
        "logbook_path": logbook_path,
        "stim_log_path": paths["stim"],
        "feedback_log_path": paths["feedback"],
        "etl_log_path": paths["etl"],
        "shutter_log_path": paths["shutter"],
        "time_log_path": paths["time"],
        "params": params,
        "stim_file": None,
        "feedback_file": None,
        "etl_file": None,
        "shutter_file": None,
        "time_file": None,
        "t0": None,
    }


def open_power_logs(session):
    """Open log files for appending during the experiment."""
    mapping = (
        ("stim_file", "stim_log_path"),
        ("feedback_file", "feedback_log_path"),
        ("etl_file", "etl_log_path"),
        ("shutter_file", "shutter_log_path"),
        ("time_file", "time_log_path"),
    )
    for handle_key, path_key in mapping:
        if session.get(handle_key) is None and session.get(path_key):
            session[handle_key] = open(session[path_key], "a", encoding="utf-8")
    return session["stim_file"], session["feedback_file"]


def _format_log_value(value):
    if value is None:
        return "nan"
    try:
        if value != value:  # NaN
            return "nan"
    except Exception:
        pass
    return f"{value}"


def append_power_sample(session, stim_mw, feedback_mw, etl_v=None, shutter_v=None, t_s=None):
    """Write one aligned sample to disk immediately.

    etl_v and shutter_v are LabJack analog volts. t_s is seconds since the
    first sample (filled in automatically if omitted).
    """
    open_power_logs(session)
    if t_s is None:
        now = datetime.now().timestamp()
        if session.get("t0") is None:
            session["t0"] = now
        t_s = now - session["t0"]
    session["stim_file"].write(f"{stim_mw}\n")
    session["feedback_file"].write(f"{feedback_mw}\n")
    session["etl_file"].write(_format_log_value(etl_v) + "\n")
    session["shutter_file"].write(_format_log_value(shutter_v) + "\n")
    session["time_file"].write(f"{t_s}\n")
    for key in ("stim_file", "feedback_file", "etl_file", "shutter_file", "time_file"):
        session[key].flush()


def close_power_logs(session):
    """Flush and close log files (call when the user presses q)."""
    for key in ("stim_file", "feedback_file", "etl_file", "shutter_file", "time_file"):
        handle = session.get(key)
        if handle is not None:
            handle.flush()
            handle.close()
            session[key] = None
