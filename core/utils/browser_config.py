import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from core.utils.config import load_config


DEFAULT_BROWSER_PROFILE_DIRNAME = "Library/Application Support/Citro Labs/ego lite"
DEFAULT_BROWSER_DEBUG_PORT = 39222
_USER_DATA_DIR_PATTERN = re.compile(r"--user-data-dir=(?P<path>.+?)(?=\s+--|\s*$)")
_DEBUG_PORT_PATTERN = re.compile(r"--remote-debugging-port=(?P<port>\d+)")

_PROFILE_MARKERS = (
    os.path.join("Default", "Network", "Cookies"),
    os.path.join("Default", "Cookies"),
    "Local State",
    "First Run",
)


def _expand_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return os.path.abspath(os.path.expanduser(path))


def get_default_browser_profile_dir() -> str:
    return str(Path.home() / DEFAULT_BROWSER_PROFILE_DIRNAME)


def browser_profile_exists(profile_dir: Optional[str]) -> bool:
    if not profile_dir:
        return False
    return os.path.isdir(profile_dir)


def browser_profile_has_state(profile_dir: Optional[str]) -> bool:
    if not profile_dir:
        return False
    return any(os.path.exists(os.path.join(profile_dir, marker)) for marker in _PROFILE_MARKERS)


def get_configured_browser_profile_dir(
    config: Optional[dict] = None,
    configured_profile: Optional[str] = None,
) -> str:
    if configured_profile is None:
        if config is None:
            config = load_config()
        configured_profile = config.get("browser_profile")
    return _expand_path(configured_profile) or get_default_browser_profile_dir()


def resolve_browser_profile_dir(
    config: Optional[dict] = None,
    configured_profile: Optional[str] = None,
) -> str:
    return get_configured_browser_profile_dir(config=config, configured_profile=configured_profile)


def get_browser_debug_port(config: Optional[dict] = None) -> int:
    if config is None:
        config = load_config()

    try:
        port = int(config.get("browser_debug_port", DEFAULT_BROWSER_DEBUG_PORT))
    except (TypeError, ValueError):
        return DEFAULT_BROWSER_DEBUG_PORT

    if 1 <= port <= 65535:
        return port
    return DEFAULT_BROWSER_DEBUG_PORT


def get_browser_runtime_config(config: Optional[dict] = None) -> dict:
    if config is None:
        config = load_config()

    configured_profile_dir = get_configured_browser_profile_dir(config=config)
    effective_profile_dir = resolve_browser_profile_dir(config=config)
    return {
        "configured_profile_dir": configured_profile_dir,
        "profile_dir": effective_profile_dir,
        "profile_fallback": False,
        "debug_port": get_browser_debug_port(config=config),
    }


def is_local_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, int(port))) == 0


def find_available_debug_port(preferred_port: int, host: str = "127.0.0.1", max_tries: int = 50) -> int:
    port = int(preferred_port)
    for _ in range(max_tries):
        if not is_local_port_open(port, host=host):
            return port
        port += 1
    return int(preferred_port)


def list_running_browser_processes(browser_executable: Optional[str] = None) -> list[dict]:
    executable = _expand_path(browser_executable)
    try:
        result = subprocess.run(
            ["ps", "-axww", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []

    processes = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        pid_str, _, command = line.partition(" ")
        pid_str = pid_str.strip()
        command = command.strip()
        if not pid_str.isdigit() or not command:
            continue

        if executable:
            if not (command == executable or command.startswith(executable + " ")):
                continue
        elif "--user-data-dir=" not in command:
            continue

        user_data_dir = None
        debug_port = None
        profile_match = _USER_DATA_DIR_PATTERN.search(command)
        if profile_match:
            user_data_dir = _expand_path(profile_match.group("path"))
        port_match = _DEBUG_PORT_PATTERN.search(command)
        if port_match:
            try:
                debug_port = int(port_match.group("port"))
            except ValueError:
                debug_port = None

        processes.append(
            {
                "pid": int(pid_str),
                "command": command,
                "user_data_dir": user_data_dir,
                "debug_port": debug_port,
            }
        )
    return processes


def find_running_browser_process(
    browser_executable: Optional[str] = None,
    profile_dir: Optional[str] = None,
    debug_port: Optional[int] = None,
) -> Optional[dict]:
    target_profile = _expand_path(profile_dir)
    target_profile_real = os.path.realpath(target_profile) if target_profile else None
    for process in list_running_browser_processes(browser_executable=browser_executable):
        if target_profile:
            proc_profile = process.get("user_data_dir")
            proc_profile_real = os.path.realpath(proc_profile) if proc_profile else None
            if proc_profile != target_profile and proc_profile_real != target_profile_real:
                continue
        if debug_port is not None and process.get("debug_port") != int(debug_port):
            continue
        return process
    return None


def prepare_browser_launch(
    browser_executable: Optional[str],
    profile_dir: str,
    preferred_debug_port: int,
) -> dict:
    exact_match = find_running_browser_process(
        browser_executable=browser_executable,
        profile_dir=profile_dir,
        debug_port=preferred_debug_port,
    )
    if exact_match and exact_match.get("debug_port") and is_local_port_open(exact_match["debug_port"]):
        return {
            "action": "reuse",
            "debug_port": exact_match["debug_port"],
            "port_changed": False,
            "process": exact_match,
        }

    same_profile = find_running_browser_process(
        browser_executable=browser_executable,
        profile_dir=profile_dir,
    )
    if same_profile and same_profile.get("debug_port") and is_local_port_open(same_profile["debug_port"]):
        return {
            "action": "reuse",
            "debug_port": same_profile["debug_port"],
            "port_changed": same_profile["debug_port"] != int(preferred_debug_port),
            "restart_required": False,
            "process": same_profile,
        }

    resolved_port = int(preferred_debug_port)
    port_conflict = is_local_port_open(resolved_port)
    if port_conflict:
        resolved_port = find_available_debug_port(resolved_port + 1)

    return {
        "action": "launch",
        "debug_port": resolved_port,
        "port_changed": resolved_port != int(preferred_debug_port),
        "restart_required": bool(same_profile),
        "process": same_profile,
    }


def terminate_browser_process(process: Optional[dict], timeout_seconds: float = 5.0) -> bool:
    if not process:
        return False

    pid = process.get("pid")
    if not pid:
        return False

    try:
        subprocess.run(["pkill", "-TERM", "-P", str(pid)], check=False)
    except Exception:
        pass

    try:
        os.kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return True
        except Exception:
            break
        time.sleep(0.2)

    try:
        subprocess.run(["pkill", "-KILL", "-P", str(pid)], check=False)
    except Exception:
        pass

    try:
        os.kill(int(pid), signal.SIGKILL)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except Exception:
        return False
    return False
