"""Pure helpers for connecting manual tunnels. No GTK imports.

All system reads/processes go through injectable seams so this module is
testable without touching the real system.
"""

import getpass
import shutil
import subprocess
import threading
import time

from . import vpn


def SSH_FLAGS(port: int, parent_port: int | None = None) -> list[str]:
    """Flag list for a SOCKS5-forwarding ssh -N process.

    The ProxyCommand hop is added only when parent_port is given; it routes
    the connection through the parent's local SOCKS port (daisy chain).
    """
    flags = [
        "-N", "-D", f"127.0.0.1:{port}",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if parent_port is not None:
        flags += [
            "-o",
            f"ProxyCommand=nc -X 5 -x 127.0.0.1:{parent_port} %h %p",
        ]
    return flags


def socks_proxy_argv(user: str, remote: str, port: int,
                     parent_port: int | None = None) -> list[str]:
    """Full argv for one background SOCKS proxy."""
    return ["ssh", *SSH_FLAGS(port, parent_port), f"{user}@{remote}"]


def next_free_port(used: set[int], start: int = 1080) -> int:
    """Smallest port >= start not in used."""
    port = start
    while port in used:
        port += 1
    return port


def resolve_chain(topo, device_id):
    """Ancestors of device_id, nearest to the pc first, excluding the target.

    The pc root is excluded (it is the local machine, never a hop), so a
    direct pc-attached tunnel yields [].
    """
    by_id = {d.id: d for d in topo.devices}
    chain = []
    cur = by_id.get(device_id)
    seen = set()
    while cur is not None:
        parent = by_id.get(cur.parent_id)
        if parent is None or parent.id == "pc" or parent.id in seen:
            break
        chain.append(parent)
        seen.add(parent.id)
        cur = parent
    return list(reversed(chain))


def nearest_socks_ancestor(topo, device_id, connections):
    """SOCKS port of the closest connected SSH ancestor, or None.

    connections maps device_id -> registry entry dicts (see TunnelManager).
    """
    for ancestor in reversed(resolve_chain(topo, device_id)):
        entry = connections.get(ancestor.id)
        if (entry and entry.get("kind") == "ssh"
                and entry.get("stage") in ("connecting", "connected")
                and entry.get("port")):
            return entry["port"]
    return None


def is_connectable(device, profiles):
    """True if this tunnel can be connected: SSH always; OpenVPN only when
    bound to an imported profile; WireGuard/Other/pc never."""
    if device.protocol == "SSH":
        return True
    if device.protocol == "OpenVPN":
        return device.profile is not None and device.profile in profiles
    return False


def tree_descendants(devices, device_id):
    """Every id reachable from device_id via parent links, DFS order.

    Used for cascade disconnect across the whole tree (auto + manual).
    """
    children = {}
    for d in devices:
        pid = d.parent_id or "pc"
        if pid == d.id:
            continue
        children.setdefault(pid, []).append(d.id)
    out = []
    stack = list(children.get(device_id, []))
    while stack:
        cid = stack.pop()
        if cid in out:
            continue
        out.append(cid)
        stack.extend(children.get(cid, []))
    return out


def dedupe_auto_tunnels(auto_tunnels, manual):
    """Drop auto-discovered tunnels whose label matches a manual tunnel's
    bound profile, so a connected manual OpenVPN node does not appear twice."""
    bound = {m.profile for m in manual if m.profile}
    return [t for t in auto_tunnels if t.label not in bound]


def _has_nc() -> bool:
    return shutil.which("nc") is not None


class TunnelManager:
    """Connection state for manual tunnels. No GTK.

    All side effects flow through injectable seams (popen, is_active,
    connect_argv, disconnect_argv) so tests never touch the real system.
    """

    def __init__(self, *, popen=subprocess.Popen,
                 is_active=vpn.is_active,
                 connect_argv=vpn.connect_argv,
                 disconnect_argv=vpn.disconnect_argv,
                 now=time.monotonic,
                 has_nc=_has_nc):
        self._popen = popen
        self._is_active = is_active
        self._connect_argv = connect_argv
        self._disconnect_argv = disconnect_argv
        self._now = now
        self._has_nc = has_nc
        self._registry = {}
        self._lock = threading.Lock()

    def connect(self, device, *, topo, profiles):
        """Bring up the ancestor chain plus the target, in order.

        Returns a list of (device_id, ok, message) per attempted node. pc and
        non-tunnel nodes report ("skipped", ok=True). A WireGuard/Other
        ancestor blocks the whole chain (its hop is deferred). Stops at the
        first failure. Already-connected nodes are skipped.
        """
        results = []
        for node in [*resolve_chain(topo, device.id), device]:
            if node.id == "pc" or node.kind != "tunnel":
                results.append((node.id, True, "skipped"))
                continue
            if node.protocol in ("WireGuard", "Other"):
                results.append((node.id, False,
                                "Connect is not supported for this tunnel type"))
                break
            if not is_connectable(node, profiles):
                # Unbound OpenVPN is visual-only here; the child proceeds.
                results.append((node.id, True, "skipped"))
                continue
            with self._lock:
                entry = self._registry.get(node.id)
                if entry is not None and entry.get("stage") in (
                        "connecting", "connected"):
                    results.append((node.id, True, "already connected"))
                    continue
            if node.protocol == "SSH":
                ok, msg = self._start_ssh(node, topo)
            else:
                ok, msg = self._start_openvpn(node)
            results.append((node.id, ok, msg))
            if not ok:
                break
        return results

    def disconnect(self, device_id, *, topo):
        """Stop a node and cascade to its whole subtree."""
        for did in [device_id, *tree_descendants(topo.devices, device_id)]:
            with self._lock:
                entry = self._registry.get(did)
            if entry is None:
                continue
            if entry["kind"] == "ssh":
                proc = entry.get("proc")
                if proc is not None:
                    try:
                        proc.terminate()
                    except OSError:
                        pass
            elif entry.get("profile"):
                try:
                    self._popen(self._disconnect_argv(entry["profile"]))
                except OSError:
                    pass
            with self._lock:
                entry["proc"] = None
                entry["stage"] = "disconnected"

    def status(self, device_id):
        """(stage, message) for a device. Re-derived from live state."""
        with self._lock:
            entry = self._registry.get(device_id)
            if entry is None:
                return ("disconnected", None)
            entry = dict(entry)
        if entry.get("stage") == "disconnected":
            return ("disconnected", None)
        stage, message = self._derive_status(entry)
        if stage != entry.get("stage"):
            with self._lock:
                cur = self._registry.get(device_id)
                if cur is not None and cur.get("stage") != "disconnected":
                    cur["stage"] = stage
        return (stage, message)

    def _derive_status(self, entry):
        """(stage, message) computed purely from live state in entry."""
        if entry["kind"] == "ssh":
            proc = entry.get("proc")
            if proc is None:
                return ("disconnected", None)
            code = proc.poll()
            if code is None:
                return ("connected", entry.get("message"))
            return ("failed", entry.get("message") or f"ssh exited {code}")
        # OpenVPN: trust systemd, re-derive 'connected' from is_active.
        profile = entry.get("profile")
        if profile:
            state = self._is_active(profile)
            if state == "active":
                return ("connected", "systemd active")
            if state == "activating":
                return ("connecting", "activating")
        proc = entry.get("proc")
        if proc is None:
            return ("disconnected", None)
        code = proc.poll()
        if code is None:
            return ("connecting", entry.get("message"))
        return ("failed", entry.get("message") or f"systemctl exited {code}")

    def ports_in_use(self):
        with self._lock:
            return {e["port"] for e in self._registry.values()
                    if e.get("port") is not None
                    and e.get("stage") in ("connecting", "connected")}

    def clear(self):
        """Terminate all live SSH procs and drop the registry (window close)."""
        with self._lock:
            procs = [e["proc"] for e in self._registry.values()
                     if e.get("proc") is not None]
            self._registry.clear()
        for proc in procs:
            try:
                proc.terminate()
            except OSError:
                pass

    def _start_ssh(self, node, topo):
        remote = node.detail
        if not remote:
            return (False, "no remote endpoint")
        used = self.ports_in_use()
        port = node.port if (node.port and node.port not in used) else \
            next_free_port(used)
        parent_port = nearest_socks_ancestor(topo, node.id, self._registry)
        if parent_port is not None and not self._has_nc():
            return (False, "netcat (nc) not found; the SOCKS hop needs it")
        user = node.user or getpass.getuser()
        argv = socks_proxy_argv(user, remote, port, parent_port)
        try:
            proc = self._popen(argv, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
        except OSError as exc:
            return (False, f"failed to start ssh: {exc}")
        with self._lock:
            self._registry[node.id] = {
                "kind": "ssh", "proc": proc, "port": port,
                "since": self._now(), "message": None, "stage": "connecting",
            }
        self._spawn_reader(node.id, proc)
        return (True, f"SOCKS on 127.0.0.1:{port}")

    def _start_openvpn(self, node):
        profile = node.profile
        if not profile:
            return (False, "no OpenVPN profile bound")
        try:
            proc = self._popen(self._connect_argv(profile))
        except OSError as exc:
            return (False, f"failed to start unit: {exc}")
        with self._lock:
            self._registry[node.id] = {
                "kind": "openvpn", "proc": proc, "port": None,
                "since": self._now(), "message": None, "stage": "connecting",
                "profile": profile,
            }
        return (True, f"starting openvpn-client@{profile}")

    def _spawn_reader(self, device_id, proc):
        """Drain the ssh process's stderr into the entry's message."""
        stderr = proc.stderr
        if stderr is None:
            return

        def drain():
            try:
                for line in stderr:
                    text = line.decode(errors="replace").rstrip()
                    if not text:
                        continue
                    with self._lock:
                        entry = self._registry.get(device_id)
                        if entry is not None:
                            entry["message"] = text
            except (OSError, ValueError):
                pass

        threading.Thread(target=drain, daemon=True).start()
