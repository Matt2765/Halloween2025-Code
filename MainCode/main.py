# main.py
import subprocess
from utils.tools import log_event

def get_git_version():
    try:
        version = subprocess.check_output(
            ["git", "describe", "--tags", "--always"],
            stderr=subprocess.STDOUT
        ).decode().strip()
        return version
    except Exception:
        return "unknown-version"

if __name__ == "__main__":
    from control.system import initialize_system
    log_event(f"SEVILLE MANOR - Copyright (c) 2025 Matthew Ruiz All Rights Reserved. Version {get_git_version()}")
    # Start main thread
    initialize_system()