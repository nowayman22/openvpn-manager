"""Pure formatting helpers. No GTK imports."""

_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def _scale(n: float) -> tuple[float, str]:
    value = float(n)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return value, unit
        value /= 1024
    return value, _UNITS[-1]


def human_bytes(n: int) -> str:
    value, unit = _scale(n)
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def human_speed(bps: float) -> str:
    return f"{human_bytes(int(bps))}/s"


def human_duration(seconds: int) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
