"""Interface detection and throughput sampling. No GTK imports."""

import os
import time


def detect_iface(sys_net_dir: str = "/sys/class/net") -> str | None:
    try:
        names = sorted(os.listdir(sys_net_dir))
    except FileNotFoundError:
        return None
    for name in names:
        if not (name.startswith("tun") or name.startswith("tap")):
            continue
        state_path = os.path.join(sys_net_dir, name, "operstate")
        try:
            state = open(state_path).read().strip()
        except OSError:
            state = "unknown"
        if state != "down":
            return name
    return None


def read_counters(iface: str, sys_net_dir: str = "/sys/class/net") -> tuple[int, int]:
    base = os.path.join(sys_net_dir, iface, "statistics")
    rx = int(open(os.path.join(base, "rx_bytes")).read().strip())
    tx = int(open(os.path.join(base, "tx_bytes")).read().strip())
    return rx, tx


class Sampler:
    def __init__(self, iface: str, reader=read_counters, clock=time.monotonic):
        self._iface = iface
        self._reader = reader
        self._clock = clock
        self._baseline: tuple[int, int] | None = None
        self._last: tuple[int, int] | None = None
        self._last_t: float | None = None

    def sample(self) -> dict:
        rx, tx = self._reader(self._iface)
        now = self._clock()
        if self._baseline is None:
            self._baseline = (rx, tx)
            self._last = (rx, tx)
            self._last_t = now
            return {"up_bps": 0.0, "down_bps": 0.0, "total_rx": 0, "total_tx": 0}
        dt = now - self._last_t if self._last_t is not None else 0.0
        drx = rx - self._last[0]
        dtx = tx - self._last[1]
        down_bps = drx / dt if dt > 0 else 0.0
        up_bps = dtx / dt if dt > 0 else 0.0
        self._last = (rx, tx)
        self._last_t = now
        return {
            "up_bps": up_bps,
            "down_bps": down_bps,
            "total_rx": rx - self._baseline[0],
            "total_tx": tx - self._baseline[1],
        }
