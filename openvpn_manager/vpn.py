"""OpenVPN systemd backend. No GTK imports.

All process execution goes through an injectable runner so this module is
testable without touching the real system.
"""

import glob
import os
import subprocess

CLIENT_DIR = "/etc/openvpn/client"


def unit_name(profile: str) -> str:
    return f"openvpn-client@{profile}"


def discover_profiles(client_dir: str = CLIENT_DIR) -> list[str]:
    names = []
    for path in glob.glob(os.path.join(client_dir, "*.conf")):
        names.append(os.path.splitext(os.path.basename(path))[0])
    return sorted(names)


def client_dir_readable(client_dir: str = CLIENT_DIR) -> bool:
    """True if the current user can list the client dir.

    The dir is 0750 openvpn:network by default; without read+execute access
    ``discover_profiles`` silently returns [] even when profiles exist.
    """
    return os.access(client_dir, os.R_OK | os.X_OK)


def connect_argv(profile: str) -> list[str]:
    return ["systemctl", "start", unit_name(profile)]


def disconnect_argv(profile: str) -> list[str]:
    return ["systemctl", "stop", unit_name(profile)]


def import_argv(src: str, client_dir: str = CLIENT_DIR) -> list[str]:
    stem = os.path.splitext(os.path.basename(src))[0]
    dest = os.path.join(client_dir, stem + ".conf")
    return ["pkexec", "cp", src, dest]


def run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)


def is_active(profile: str, runner=run) -> str:
    cp = runner(["systemctl", "is-active", unit_name(profile)])
    return (cp.stdout or "").strip()


def unit_property(profile: str, prop: str, runner=run) -> str:
    cp = runner(["systemctl", "show", "-p", prop, "--value", unit_name(profile)])
    return (cp.stdout or "").strip()


def needs_credentials(conf_path: str) -> bool:
    """True if the profile has a bare ``auth-user-pass`` directive.

    A bare directive (no file argument) makes OpenVPN block on an interactive
    username/password prompt that never reaches the GUI. If it already points
    at a credentials file, no prompt is needed.
    """
    try:
        with open(conf_path) as f:
            for line in f:
                parts = line.split()
                if parts and parts[0] == "auth-user-pass":
                    return len(parts) < 2
    except OSError:
        return False
    return False


#: Root-owned copy installed by install.sh so the polkit rule can authorize it
#: without allowing the user to tamper with the script.
_HELPER_INSTALLED_PATH = "/usr/lib/openvpn-manager/helper.sh"


def helper_path() -> str:
    """Absolute path to the privileged helper script.

    Prefers the root-owned installed copy; falls back to the copy shipped in
    the package when running uninstalled (e.g. from a checkout).
    """
    if os.path.exists(_HELPER_INSTALLED_PATH):
        return _HELPER_INSTALLED_PATH
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "helper.sh")


def setcreds_argv(profile: str, helper: str) -> list[str]:
    return ["pkexec", helper, "set-creds", profile]


def parse_remote(conf_path: str) -> tuple[str | None, str | None]:
    remote = None
    proto = None
    try:
        with open(conf_path) as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "remote" and len(parts) >= 2:
                    remote = parts[1]
                elif parts[0] == "proto" and len(parts) >= 2:
                    proto = parts[1]
    except OSError:
        return None, None
    return remote, proto
