# Manual Tunnel Connect (Daisy-Chained SOCKS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let manual SSH and OpenVPN tunnels in the topology tree actually connect and disconnect, with a live per-node status dot and daisy-chained SOCKS proxies.

**Architecture:** A new pure, GTK-free module `openvpn_manager/tunnels.py` owns argv construction, chain resolution, and a `TunnelManager` state machine (registry keyed by device id, injectable `popen`/`is_active` seams). The `Device` dataclass gains three optional persistence fields (`profile`, `user`, `port`) appended after `protocol`. The app wires the manager into its 1-second tick (dot painting), the node context menu (Connect/Disconnect toggle), and the tunnel dialog (SSH user/port + profile binding).

**Tech Stack:** Python 3.14, GTK4/libadwaita, `ssh -N -D` background processes, OpenBSD `nc -X 5` ProxyCommand hops, `systemctl` for OpenVPN, pytest with injectable runners.

## Global Constraints

- `tunnels.py` must not import GTK or any `gi` module — it is unit-testable in the venv.
- All process execution in `tunnels.py` goes through injectable seams (`popen`, `is_active`, `connect_argv`, `disconnect_argv`) so tests never touch the real system.
- SSH auth is keys/agent only: always pass `-o BatchMode=yes`, never a password.
- The `ProxyCommand` hop is added only when the tunnel's parent is a connected SSH tunnel; the value is `nc -X 5 -x 127.0.0.1:<parentPort> %h %p`.
- `nc` (SOCKS5-capable, i.e. OpenBSD netcat) is required only when a parent hop exists; check with `shutil.which("nc")` and return a clear error when missing.
- `Device` new fields are appended after `protocol` so existing positional construction is unaffected.
- Existing config files without `profile`/`user`/`port` load unchanged (all three default to `None`).
- WireGuard and non-profile-bound OpenVPN tunnels are never connectable; their context-menu Connect is disabled with a note.
- Connecting a nested tunnel auto-connects every ancestor first (nearest to pc first); disconnecting a node cascades to its descendants.
- The app repaints connection dots on its existing 1-second tick, so a dropped ssh process or stopped unit flips the node back automatically.
- The existing test suite must still pass. Note: `tests/test_vpn.py::test_helper_path_points_at_shipped_script` fails on machines where `/usr/lib/openvpn-manager/helper.sh` is installed (it asserts the uninstalled fallback path) — this is pre-existing and environmental, not caused by this work.
- Run pure tests with `.venv/bin/python -m pytest`. GTK-dependent tests skip in the venv (no `gi`); run them with system `python3` which has `gi`.

---

### Task 1: Device fields and manual-tunnel persistence

**Files:**
- Modify: `openvpn_manager/topology.py:24-35` (Device dataclass), `:430-462` (load_manual_tunnels), `:465-492` (save_manual_tunnels), `:564-577` (update_manual_tunnel)
- Test: `tests/test_topology.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Device.profile: str | None`, `Device.user: str | None`, `Device.port: int | None` (all default `None`); `update_manual_tunnel(..., profile=None, user=None, port=None)`; `load_manual_tunnels` / `save_manual_tunnels` round-trip the three fields.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_topology.py`:

```python
def test_save_and_load_manual_tunnels_with_new_fields(tmp_path):
    dev = Device(id="manual:x", kind="tunnel", label="edge",
                 parent_id="pc", manual=True, protocol="OpenVPN",
                 detail="vpn.example", profile="work", user="alice", port=1080)
    topology.save_manual_tunnels([dev], tmp_path / "t.toml")
    loaded = topology.load_manual_tunnels(tmp_path / "t.toml")
    assert len(loaded) == 1
    assert loaded[0].profile == "work"
    assert loaded[0].user == "alice"
    assert loaded[0].port == 1080


def test_load_manual_tunnels_without_new_fields(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text(
        '[[tunnels]]\nid = "manual:x"\nlabel = "a"\nparent_id = "pc"\n'
        'remote = "10.0.0.1"\nprotocol = "SSH"\n')
    loaded = topology.load_manual_tunnels(p)
    assert loaded[0].profile is None
    assert loaded[0].user is None
    assert loaded[0].port is None


def test_update_manual_tunnel_new_fields():
    dev = Device(id="manual:x", kind="tunnel", label="a", parent_id="pc",
                 manual=True, protocol="SSH", detail="h")
    out = topology.update_manual_tunnel(
        [dev], "manual:x", label="b", parent_id="pc", remote="h2",
        protocol="OpenVPN", profile="work", user="bob", port=1090)
    assert out[0].profile == "work"
    assert out[0].user == "bob"
    assert out[0].port == 1090
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: the three new tests FAIL — `Device.__init__()` got unexpected keyword arguments `profile`/`user`/`port`.

- [ ] **Step 3: Add the three Device fields**

In `openvpn_manager/topology.py`, after the `protocol` line:

```python
    protocol: str | None = None     # "SSH" | "WireGuard" | "OpenVPN" | "Other"
    profile: str | None = None      # imported OpenVPN profile bound to this tunnel
    user: str | None = None         # SSH username; defaults to current user
    port: int | None = None         # fixed local SOCKS port; None = auto-assign
```

- [ ] **Step 4: Round-trip the fields in load/save**

In `load_manual_tunnels`, extend the `Device(...)` construction:

```python
        dev = Device(
            id=entry.get("id") or f"manual:{uuid.uuid4().hex[:8]}",
            kind="tunnel",
            label=entry.get("label", "tunnel"),
            parent_id=entry.get("parent_id", "pc"),
            manual=True,
            protocol=entry.get("protocol"),
            detail=entry.get("remote"),
            profile=entry.get("profile"),
            user=entry.get("user"),
            port=entry.get("port"),
        )
```

In `save_manual_tunnels`, extend the per-device entry dict:

```python
        entries.append({
            "id": dev.id,
            "label": dev.label,
            "parent_id": dev.parent_id or "pc",
            "remote": dev.detail,
            "protocol": dev.protocol,
            "profile": dev.profile,
            "user": dev.user,
            "port": dev.port,
        })
```

(The writer already skips `None` values, so legacy files stay unchanged.)

- [ ] **Step 5: Extend `update_manual_tunnel`**

Replace the signature and body:

```python
def update_manual_tunnel(tunnels, tunnel_id, *, label, parent_id, remote,
                         protocol, profile=None, user=None, port=None):
    """Replace the matching manual tunnel's editable fields in place.

    The tunnel's id and manual flag are preserved. Missing id returns the
    input list unchanged.
    """
    for t in tunnels:
        if t.id == tunnel_id:
            t.label = label
            t.parent_id = parent_id
            t.protocol = protocol
            t.detail = remote
            t.profile = profile
            t.user = user
            t.port = port
    return tunnels
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: PASS (all tests in the file).

- [ ] **Step 7: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: persist profile, user, and SOCKS port on manual tunnels"
```

---

### Task 2: Pure argv builders and port allocation

**Files:**
- Create: `openvpn_manager/tunnels.py`
- Test: `tests/test_tunnels.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `SSH_FLAGS(port: int, parent_port: int | None = None) -> list[str]`; `socks_proxy_argv(user: str, remote: str, port: int, parent_port: int | None = None) -> list[str]`; `next_free_port(used: set[int], start: int = 1080) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tunnels.py`:

```python
"""Unit tests for the pure tunnel backend. No GTK."""

from openvpn_manager import tunnels


def test_socks_proxy_argv_direct():
    argv = tunnels.socks_proxy_argv("alice", "10.0.0.5", 1080)
    assert argv[0] == "ssh"
    assert "-N" in argv
    assert argv[argv.index("-D") + 1] == "127.0.0.1:1080"
    assert "alice@10.0.0.5" in argv
    assert not any("ProxyCommand" in o for o in argv)
    assert "-o" in argv


def test_socks_proxy_argv_with_parent_hop():
    argv = tunnels.socks_proxy_argv("alice", "10.0.0.5", 1081,
                                    parent_port=1080)
    joined = " ".join(argv)
    assert "ProxyCommand=nc -X 5 -x 127.0.0.1:1080 %h %p" in joined
    assert "127.0.0.1:1081" in joined
    assert "alice@10.0.0.5" in joined


def test_socks_proxy_argv_always_batch_mode():
    argv = tunnels.socks_proxy_argv("bob", "1.2.3.4", 1080)
    assert "BatchMode=yes" in argv


def test_next_free_port_skips_used():
    assert tunnels.next_free_port({1080, 1081, 1082}) == 1083


def test_next_free_port_respects_start():
    assert tunnels.next_free_port({}, start=1200) == 1200
    assert tunnels.next_free_port({1200}, start=1200) == 1201
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tunnels.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'openvpn_manager.tunnels'`.

- [ ] **Step 3: Create `tunnels.py` with the builders**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tunnels.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add openvpn_manager/tunnels.py tests/test_tunnels.py
git commit -m "feat: add pure SSH socks argv and port allocation helpers"
```

---

### Task 3: Chain resolution, connectability, descendants, dedupe

**Files:**
- Modify: `openvpn_manager/tunnels.py`
- Test: `tests/test_tunnels.py`

**Interfaces:**
- Consumes: `Device`, `Topology` from `.topology`.
- Produces: `resolve_chain(topo, device_id) -> list[Device]` (root-first, excluding target); `nearest_socks_ancestor(topo, device_id, connections) -> int | None`; `is_connectable(device, profiles) -> bool`; `tree_descendants(devices, device_id) -> list[str]`; `dedupe_auto_tunnels(auto_tunnels, manual) -> list[Device]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tunnels.py`:

```python
from openvpn_manager.topology import Device, Topology


def _pc():
    return Device(id="pc", kind="pc", label="host")


def _ssh(id, label, parent_id="pc", **kw):
    kw.setdefault("manual", True)
    kw.setdefault("protocol", "SSH")
    kw.setdefault("detail", "10.0.0.5")
    return Device(id=id, kind="tunnel", label=label, parent_id=parent_id, **kw)


def _simple_topo():
    pc = _pc()
    a = _ssh("manual:a", "a")
    return Topology(hostname="host", root=pc, devices=[pc, a], edges=[])


def _nested_topo():
    pc = _pc()
    a = _ssh("manual:a", "a")
    b = _ssh("manual:b", "b", parent_id="manual:a")
    return Topology(hostname="host", root=pc, devices=[pc, a, b], edges=[])


def test_resolve_chain_orders_ancestors():
    assert [d.id for d in tunnels.resolve_chain(_nested_topo(), "manual:b")] \
        == ["manual:a"]


def test_resolve_chain_direct_is_empty():
    assert tunnels.resolve_chain(_simple_topo(), "manual:a") == []


def test_nearest_socks_ancestor_finds_closest_connected():
    conns = {"manual:a": {"kind": "ssh", "port": 1080,
                          "stage": "connected"}}
    assert tunnels.nearest_socks_ancestor(
        _nested_topo(), "manual:b", conns) == 1080


def test_nearest_socks_ancestor_none_when_not_connected():
    assert tunnels.nearest_socks_ancestor(_nested_topo(), "manual:b", {}) \
        is None


def test_nearest_socks_ancestor_ignores_non_ssh():
    conns = {"manual:a": {"kind": "openvpn", "port": None,
                          "stage": "connected"}}
    assert tunnels.nearest_socks_ancestor(
        _nested_topo(), "manual:b", conns) is None


def test_is_connectable():
    assert tunnels.is_connectable(_ssh("m:a", "a"), [])
    ovpn = Device(id="m:v", kind="tunnel", label="v", parent_id="pc",
                  manual=True, protocol="OpenVPN", profile="work")
    assert tunnels.is_connectable(ovpn, ["work"])
    assert not tunnels.is_connectable(ovpn, ["other"])
    wg = Device(id="m:w", kind="tunnel", label="w", parent_id="pc",
                manual=True, protocol="WireGuard", detail="10.0.0.9")
    assert not tunnels.is_connectable(wg, [])
    assert not tunnels.is_connectable(_pc(), [])


def test_tree_descendants_multilevel():
    devices = [
        _pc(),
        _ssh("m:a", "a"),
        _ssh("m:b", "b", parent_id="m:a"),
        _ssh("m:c", "c", parent_id="m:a"),
        _ssh("m:d", "d", parent_id="m:b"),
    ]
    got = tunnels.tree_descendants(devices, "m:a")
    assert set(got) == {"m:b", "m:c", "m:d"}


def test_dedupe_auto_tunnels_drops_bound_profile():
    auto = [Device(id="tun:work", kind="tunnel", label="work"),
            Device(id="tun:home", kind="tunnel", label="home")]
    manual = [Device(id="m:v", kind="tunnel", label="v", manual=True,
                     protocol="OpenVPN", profile="work")]
    out = tunnels.dedupe_auto_tunnels(auto, manual)
    assert [t.label for t in out] == ["home"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tunnels.py -q`
Expected: FAIL — `AttributeError: module 'openvpn_manager.tunnels' has no attribute 'resolve_chain'`.

- [ ] **Step 3: Implement the five helpers**

Append to `openvpn_manager/tunnels.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tunnels.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add openvpn_manager/tunnels.py tests/test_tunnels.py
git commit -m "feat: add chain resolution and connectability for tunnels"
```

---

### Task 4: TunnelManager state machine

**Files:**
- Modify: `openvpn_manager/tunnels.py`
- Test: `tests/test_tunnels.py`

**Interfaces:**
- Consumes: `resolve_chain`, `nearest_socks_ancestor`, `is_connectable`, `tree_descendants`, `next_free_port`, `socks_proxy_argv` from Task 2/3; `vpn.connect_argv` / `vpn.disconnect_argv` / `vpn.is_active`.
- Produces: `TunnelManager` with methods `connect(device, *, topo, profiles) -> list[(device_id, ok, message)]`, `disconnect(device_id, *, topo)`, `status(device_id) -> (stage, message)`, `ports_in_use() -> set[int]`, `clear()`. Registry entry shape: `{"kind", "proc", "port", "since", "message", "stage", "profile"}`. Stages: `disconnected | connecting | connected | failed`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tunnels.py`:

```python
class FakeProc:
    def __init__(self, poll_value=None):
        self._poll = poll_value
        self.stderr = None
        self.argv = None
        self.terminated = False

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True


def _fake_popen(poll_value=None):
    calls = []

    def popen(argv, **kwargs):
        proc = FakeProc(poll_value)
        proc.argv = argv
        calls.append(proc)
        return proc

    return calls, popen


def _topo_with(*devices):
    return Topology(hostname="host", root=_pc(),
                    devices=[_pc(), *devices], edges=[])


def test_connect_ssh_allocates_port_and_starts():
    calls, popen = _fake_popen()
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _simple_topo()
    dev = next(d for d in topo.devices if d.id == "manual:a")
    results = mgr.connect(dev, topo=topo, profiles=[])
    assert results[-1][:2] == ("manual:a", True)
    assert calls[-1].argv[0] == "ssh"
    assert "127.0.0.1:1080" in " ".join(calls[-1].argv)
    assert mgr.ports_in_use() == {1080}


def test_connect_child_uses_parent_socks_port():
    calls, popen = _fake_popen()
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _nested_topo()
    dev = next(d for d in topo.devices if d.id == "manual:b")
    results = mgr.connect(dev, topo=topo, profiles=[])
    assert all(ok for _id, ok, _msg in results)
    child_argv = " ".join(calls[-1].argv)
    assert "ProxyCommand=nc -X 5 -x 127.0.0.1:1080 %h %p" in child_argv
    assert mgr.ports_in_use() == {1080, 1081}


def test_connect_fixed_port_used_when_free():
    dev = _ssh("manual:a", "a", port=2222)
    calls, popen = _fake_popen()
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    results = mgr.connect(dev, topo=_topo_with(dev), profiles=[])
    assert results[-1][1] is True
    assert "127.0.0.1:2222" in " ".join(calls[-1].argv)


def test_connect_fixed_port_conflict_falls_back():
    a = _ssh("manual:a", "a", port=1080)
    z = _ssh("manual:z", "z")
    calls, popen = _fake_popen()
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _topo_with(a, z)
    mgr.connect(z, topo=topo, profiles=[])  # takes 1080
    results = mgr.connect(a, topo=topo, profiles=[])
    assert results[-1][1] is True
    assert "127.0.0.1:1081" in " ".join(calls[-1].argv)


def test_connect_stops_at_first_failure():
    def popen(argv, **kwargs):
        raise OSError("boom")

    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _nested_topo()
    dev = next(d for d in topo.devices if d.id == "manual:b")
    results = mgr.connect(dev, topo=topo, profiles=[])
    attempted = [r[0] for r in results if r[0] != "pc"]
    assert attempted == ["manual:a"]
    assert results[-1][1] is False


def test_connect_child_under_wireguard_fails():
    wg = Device(id="manual:w", kind="tunnel", label="w", parent_id="pc",
                manual=True, protocol="WireGuard", detail="10.0.0.9")
    child = _ssh("manual:c", "c", parent_id="manual:w")
    calls, popen = _fake_popen()
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _topo_with(wg, child)
    results = mgr.connect(child, topo=topo, profiles=[])
    assert results[-1][1] is False
    assert not calls  # nothing spawned


def test_connect_skips_already_connected():
    calls, popen = _fake_popen()
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _simple_topo()
    dev = next(d for d in topo.devices if d.id == "manual:a")
    mgr.connect(dev, topo=topo, profiles=[])
    results = mgr.connect(dev, topo=topo, profiles=[])
    assert results[-1][1] is True
    assert results[-1][2] == "already connected"
    assert len(calls) == 1  # no second process


def test_status_ssh_running_is_connected():
    calls, popen = _fake_popen(poll_value=None)
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _simple_topo()
    dev = next(d for d in topo.devices if d.id == "manual:a")
    mgr.connect(dev, topo=topo, profiles=[])
    assert mgr.status("manual:a")[0] == "connected"


def test_status_ssh_exited_is_failed():
    calls, popen = _fake_popen(poll_value=255)
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _simple_topo()
    dev = next(d for d in topo.devices if d.id == "manual:a")
    mgr.connect(dev, topo=topo, profiles=[])
    stage, msg = mgr.status("manual:a")
    assert stage == "failed"
    assert "255" in msg


def test_status_openvpn_derives_from_unit():
    calls, popen = _fake_popen(poll_value=None)
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "active")
    ovpn = Device(id="manual:v", kind="tunnel", label="v", parent_id="pc",
                  manual=True, protocol="OpenVPN", profile="work")
    mgr.connect(ovpn, topo=_topo_with(ovpn), profiles=["work"])
    assert mgr.status("manual:v")[0] == "connected"
    assert mgr.ports_in_use() == set()
    assert calls[-1].argv == ["systemctl", "start", "openvpn-client@work"]


def test_status_missing_is_disconnected():
    mgr = tunnels.TunnelManager(popen=_fake_popen()[1])
    assert mgr.status("manual:zzz") == ("disconnected", None)


def test_disconnect_cascades_to_descendants():
    calls, popen = _fake_popen()
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _nested_topo()
    dev = next(d for d in topo.devices if d.id == "manual:b")
    mgr.connect(dev, topo=topo, profiles=[])
    mgr.disconnect("manual:a", topo=topo)
    assert mgr.status("manual:a")[0] == "disconnected"
    assert mgr.status("manual:b")[0] == "disconnected"
    assert all(p.terminated for p in calls)


def test_disconnect_openvpn_stops_unit():
    calls, popen = _fake_popen()
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    ovpn = Device(id="manual:v", kind="tunnel", label="v", parent_id="pc",
                  manual=True, protocol="OpenVPN", profile="work")
    mgr.connect(ovpn, topo=_topo_with(ovpn), profiles=["work"])
    mgr.disconnect("manual:v", topo=_topo_with(ovpn))
    assert calls[-1].argv == ["systemctl", "stop", "openvpn-client@work"]


def test_clear_terminates_all_ssh_procs():
    calls, popen = _fake_popen()
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _nested_topo()
    dev = next(d for d in topo.devices if d.id == "manual:b")
    mgr.connect(dev, topo=topo, profiles=[])
    mgr.clear()
    assert all(p.terminated for p in calls)
    assert mgr.ports_in_use() == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tunnels.py -q`
Expected: FAIL — `AttributeError: module 'openvpn_manager.tunnels' has no attribute 'TunnelManager'`.

- [ ] **Step 3: Implement TunnelManager**

Append to `openvpn_manager/tunnels.py`:

```python
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
                 now=time.monotonic):
        self._popen = popen
        self._is_active = is_active
        self._connect_argv = connect_argv
        self._disconnect_argv = disconnect_argv
        self._now = now
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
        if parent_port is not None and not _has_nc():
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tunnels.py -q`
Expected: PASS. Then run the whole suite to confirm nothing regressed:
`.venv/bin/python -m pytest -q` (the one pre-existing `test_helper_path...` failure may appear on installed machines — see Global Constraints).

- [ ] **Step 5: Commit**

```bash
git add openvpn_manager/tunnels.py tests/test_tunnels.py
git commit -m "feat: add TunnelManager state machine for tunnel connections"
```

---

### Task 5: Connection-stage status dots in the diagram

**Files:**
- Modify: `openvpn_manager/topology_diagram.py`
- Test: `tests/test_topology_view.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `tunnel_dot(status) -> tuple | None` (module-level pure helper). `set_tunnel_status` already accepts any string; only the painting branches change.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topology_view.py`:

```python
def test_tunnel_dot_maps_connection_stages():
    from openvpn_manager.topology_diagram import tunnel_dot
    assert tunnel_dot("connected") == tunnel_dot("ok")
    assert tunnel_dot("connecting") == tunnel_dot("testing")
    assert tunnel_dot("failed") == tunnel_dot("fail")
    assert tunnel_dot("disconnected") is None
    assert tunnel_dot(None) is None
```

(Note: this test needs `gi`; it skips in the venv like the other tests in this file. Verify locally with system `python3 -m pytest` when `gi` is present.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_topology_view.py -q` (system python with gi)
Expected: FAIL — `ImportError: cannot import name 'tunnel_dot'`.

- [ ] **Step 3: Add the mapping and rewrite the dot branch**

In `openvpn_manager/topology_diagram.py`, after the color constants:

```python
#: Tunnel status -> (fill rgb, radius). Statuses without a mapping paint a
#: faint outline instead.
_TUNNEL_DOT = {
    "connected": (0.45, 0.75, 0.35, 5),
    "ok": (0.45, 0.75, 0.35, 5),
    "connecting": (0.95, 0.65, 0.10, 5),
    "testing": (0.95, 0.65, 0.10, 5),
    "failed": (0.85, 0.35, 0.32, 5),
    "fail": (0.85, 0.35, 0.32, 5),
}


def tunnel_dot(status):
    """Return (r, g, b, radius) for a tunnel status, or None for outline."""
    return _TUNNEL_DOT.get(status)
```

Replace the connectivity-test dot block in `_draw_node`:

```python
        # connectivity test / connection status dot (bottom-left)
        if dev.kind == "tunnel":
            dot = tunnel_dot(self._tunnel_status.get(dev.id))
            sx, sy = x + 14, y + h - 14
            if dot is not None:
                cr.set_source_rgb(*dot[:3])
                cr.arc(sx, sy, dot[3], 0, 2 * 3.14159)
                cr.fill()
            else:
                cr.set_source_rgba(0.45, 0.48, 0.55, 0.5)
                cr.arc(sx, sy, 4, 0, 2 * 3.14159)
                cr.set_line_width(1.0)
                cr.stroke()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_topology_view.py -q` (system python with gi)
Expected: PASS (3 existing + 1 new).

- [ ] **Step 5: Commit**

```bash
git add openvpn_manager/topology_diagram.py tests/test_topology_view.py
git commit -m "feat: paint connection-stage dots on tunnel cards"
```

---

### Task 6: Dialog fields, profile binding, and dedupe

**Files:**
- Modify: `openvpn_manager/app.py` (`_tunnel_dialog`, `_refresh_topology`), imports
- Test: manual (GTK dialog per project convention)

**Interfaces:**
- Consumes: `Device.profile/user/port` (Task 1), `dedupe_auto_tunnels` (Task 3).
- Produces: manual tunnels saved with `profile`/`user`/`port`; auto tunnels deduped against bound profiles.

- [ ] **Step 1: Add the module import**

In `openvpn_manager/app.py`, add after the existing `from .topology import (...)` import:

```python
from . import tunnels as tunnel_backend
```

- [ ] **Step 2: Wire dedupe into `_refresh_topology`**

Replace the two lines that load manual tunnels and build the tree:

```python
        manual = load_manual_tunnels()
        auto_tunnels = tunnel_backend.dedupe_auto_tunnels(auto_tunnels, manual)
        hostname = socket.gethostname() or "This machine"
```

- [ ] **Step 3: Add SSH user/port fields and profile binding to `_tunnel_dialog`**

The source picker is currently add-mode-only. Make it always present, add the SSH-only fields box, and record `profile`/`user`/`port` on save.

Replace the `source_combo` block (currently `if tunnel is None:` guarded) with:

```python
        # Source picker: Free-form or an imported .ovpn profile.
        source_combo = Adw.ComboRow(title="Source")
        source_combo.set_model(Gtk.StringList.new(
            ["Free-form", *self._profiles]))
        source_combo.set_selected(0)
        group.add(source_combo)
```

After the `proto_combo` block and before `body.append(group)`, keep the group but add the SSH-only fields box right after `body.append(group)`:

```python
        # SSH-only fields: username and optional fixed SOCKS port.
        ssh_fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        user_entry = Gtk.Entry(placeholder_text="Username (SSH)")
        default_user = tunnel.user if (tunnel is not None and tunnel.user) \
            else getpass.getuser()
        user_entry.set_text(default_user)
        ssh_fields.append(user_entry)
        port_entry = Gtk.Entry(placeholder_text="Local SOCKS port (optional)")
        if tunnel is not None and tunnel.port is not None:
            port_entry.set_text(str(tunnel.port))
        ssh_fields.append(port_entry)
        body.append(ssh_fields)

        def refresh_ssh_fields():
            proto_names_ = proto_names
            ssh_fields.set_visible(
                proto_combo.get_selected() >= 0
                and proto_names_[proto_combo.get_selected()] == "SSH")

        proto_combo.connect("notify::selected",
                            lambda *_: refresh_ssh_fields())
        refresh_ssh_fields()
```

Then update `on_source_changed` so it only pre-fills empty label/remote, and the pre-selection logic for edit mode. Replace the existing `on_source_changed` and the `if source_combo is not None:` connect block with:

```python
        def on_source_changed(*_args):
            idx = source_combo.get_selected()
            if idx <= 0 or idx > len(self._profiles):
                return
            profile = self._profiles[idx - 1]
            remote, _proto = vpn.parse_remote(
                f"{vpn.CLIENT_DIR}/{profile}.conf")
            if not label_entry.get_text().strip():
                label_entry.set_text(profile)
            if remote and not remote_entry.get_text().strip():
                remote_entry.set_text(remote)
            proto_combo.set_selected(proto_names.index("OpenVPN"))

        source_combo.connect("notify::selected", on_source_changed)
        if tunnel is not None and tunnel.profile in self._profiles:
            source_combo.set_selected(1 + self._profiles.index(tunnel.profile))
        elif source_profile is not None and source_profile in self._profiles:
            source_combo.set_selected(1 + self._profiles.index(source_profile))
            on_source_changed()
```

Then update the save path inside `on_response`. Replace the field-collection part:

```python
            parent_idx = parent_combo.get_selected()
            parent_id_sel = (parent_devices[parent_idx].id
                             if 0 <= parent_idx < len(parent_devices)
                             else "pc")
            proto_model = proto_combo.get_model()
            proto = proto_model.get_string(proto_combo.get_selected()) if (
                proto_combo.get_selected() >= 0) else "SSH"
            profile = None
            src_idx = source_combo.get_selected()
            if proto == "OpenVPN" and 0 < src_idx <= len(self._profiles):
                profile = self._profiles[src_idx - 1]
            user = None
            port = None
            if proto == "SSH":
                user = user_entry.get_text().strip() or None
                port_text = port_entry.get_text().strip()
                if port_text:
                    try:
                        port = int(port_text)
                    except ValueError:
                        port = None  # invalid -> auto-assign at connect time
            existing = load_manual_tunnels()
            if tunnel is not None:
                existing = update_manual_tunnel(
                    existing, tunnel.id, label=label, parent_id=parent_id_sel,
                    remote=remote, protocol=proto, profile=profile,
                    user=user, port=port)
                save_manual_tunnels(existing)
                self._refresh_topology()
                self._toast.add_toast(
                    Adw.Toast.new(f"Tunnel '{label}' updated"))
            else:
                import uuid
                new_tun = Device(
                    id=f"manual:{uuid.uuid4().hex[:8]}",
                    kind="tunnel", label=label, parent_id=parent_id_sel,
                    manual=True, protocol=proto, detail=remote,
                    profile=profile, user=user, port=port)
                existing.append(new_tun)
                save_manual_tunnels(existing)
                self._refresh_topology()
                self._toast.add_toast(
                    Adw.Toast.new(f"Tunnel '{label}' added"))
```

- [ ] **Step 4: Run the pure suite and a syntax check**

Run: `.venv/bin/python -m pytest -q` — expect the same result as before (pure tests pass; only the pre-existing installed-helper failure, if any).
Run: `python3 -m openvpn_manager 2>&1 | head -5` (or import check `python3 -c "from openvpn_manager.app import OpenVpnManagerApp"`) to confirm the module imports cleanly.

- [ ] **Step 5: Commit**

```bash
git add openvpn_manager/app.py
git commit -m "feat: add SSH user/port and profile binding to tunnel dialog, dedupe auto tunnels"
```

---

### Task 7: App wiring — manager, tick dots, context menu, connect/disconnect

**Files:**
- Modify: `openvpn_manager/app.py`
- Test: manual (context menu, popover, dots per project convention)

**Interfaces:**
- Consumes: `TunnelManager` (Task 4), `is_connectable` (Task 3), `tunnel_dot` stages (Task 5), `_prompt_credentials`/`_save_creds_and_connect`.
- Produces: `self._tunnels` instance; Connect/Disconnect toggle in the node context menu; dot repaint each tick; window close tears down procs.

- [ ] **Step 1: Create the manager and hook window close**

In `Window.__init__`, after the `self._palette` setup (near the top), add:

```python
        self._tunnels = tunnel_backend.TunnelManager()
        self.connect("close-request", self._on_close_request)
```

Add the close handler near the other handlers:

```python
    def _on_close_request(self, *_args):
        self._tunnels.clear()
        return False  # allow the window to close
```

- [ ] **Step 2: Paint connection dots on every tick**

Add a new method and call it at the top of `_tick`. Modify `_tick` so the first line inside `try:` is:

```python
        self._paint_tunnel_connections()
```

Add the method (place it right after `_tick`):

```python
    def _paint_tunnel_connections(self):
        diagram = self._topo_view._diagram
        topo = diagram._topo
        if topo is None:
            return
        for dev in topo.devices:
            if dev.kind == "tunnel" and dev.manual:
                stage, _msg = self._tunnels.status(dev.id)
                diagram.set_tunnel_status(dev.id, stage)
```

- [ ] **Step 3: Add the Connect/Disconnect toggle to the context menu**

In `_on_node_context`, replace the whole `if device.kind == "tunnel":` block (currently just the Test button) with:

```python
        if device.kind == "tunnel":
            if tunnel_backend.is_connectable(device, self._profiles):
                stage, _msg = self._tunnels.status(device.id)
                label = ("Disconnect" if stage in ("connected", "connecting")
                         else "Connect…")
                conn_btn = Gtk.Button(label=label)
                conn_btn.add_css_class("flat")
                conn_btn.set_halign(Gtk.Align.START)
                if stage in ("connected", "connecting"):
                    conn_btn.connect("clicked", lambda *_: (
                        popover.popdown(), self._on_disconnect_tunnel(device)))
                else:
                    conn_btn.connect("clicked", lambda *_: (
                        popover.popdown(), self._on_connect_tunnel(device)))
                box.append(conn_btn)
            elif device.manual:
                disabled = Gtk.Button(label="Connect…")
                disabled.add_css_class("flat")
                disabled.set_halign(Gtk.Align.START)
                disabled.set_sensitive(False)
                disabled.set_tooltip_text(
                    "Connect is not supported for this tunnel type.")
                box.append(disabled)
            test_btn = Gtk.Button(label="Test connectivity…")
            test_btn.add_css_class("flat")
            test_btn.set_halign(Gtk.Align.START)
            test_btn.set_sensitive(bool(device.detail))
            test_btn.connect("clicked", lambda *_: (
                popover.popdown(), self._on_test_tunnel(device)))
            box.append(test_btn)
```

- [ ] **Step 4: Add the connect/disconnect handlers and credential reuse**

Add these methods (e.g. right after `_on_test_tunnel`):

```python
    def _on_connect_tunnel(self, device):
        if device.protocol == "OpenVPN" and device.profile:
            conf = f"{vpn.CLIENT_DIR}/{device.profile}.conf"
            if vpn.needs_credentials(conf):
                self._prompt_credentials(
                    device.profile,
                    on_done=lambda: self._on_connect_tunnel(device))
                return
        topo = self._topo_view._diagram._topo
        if topo is None:
            return
        results = self._tunnels.connect(
            device, topo=topo, profiles=self._profiles)
        self._finish_tunnel_connect(results)

    def _finish_tunnel_connect(self, results):
        self._refresh_topology()
        for device_id, ok, msg in results:
            if msg == "skipped":
                continue
            toast = Adw.Toast.new(
                f"{self._tunnel_label(device_id)}: {msg}")
            if not ok:
                toast.set_timeout(4)
            self._toast.add_toast(toast)

    def _on_disconnect_tunnel(self, device):
        topo = self._topo_view._diagram._topo
        if topo is None:
            return
        self._tunnels.disconnect(device.id, topo=topo)
        self._refresh_topology()
        self._toast.add_toast(
            Adw.Toast.new(f"Disconnected '{device.label}'"))

    def _tunnel_label(self, device_id):
        topo = self._topo_view._diagram._topo
        if topo:
            for d in topo.devices:
                if d.id == device_id:
                    return d.label
        return device_id
```

Modify `_prompt_credentials` to accept and forward an `on_done` callback. Change its signature and the connect response:

```python
    def _prompt_credentials(self, profile, on_done=None):
```

and inside `on_response`:

```python
        def on_response(_d, response):
            if response == "connect":
                self._save_creds_and_connect(
                    profile, user_entry.get_text(), pass_entry.get_text(),
                    on_done=on_done)
```

Modify `_save_creds_and_connect` to accept `on_done`:

```python
    def _save_creds_and_connect(self, profile, username, password,
                                on_done=None):
```

and change its `on_done=` argument at the `_spawn(...)` call to:

```python
                    on_done=on_done or (lambda: self._connect(profile)),
```

- [ ] **Step 5: Syntax check and full suite**

Run: `python3 -c "from openvpn_manager.app import OpenVpnManagerApp"` and `.venv/bin/python -m pytest -q`.
Expected: clean import; pure suite passes (same pre-existing helper failure, if any, only).

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/app.py
git commit -m "feat: connect and disconnect manual tunnels from the context menu"
```

---

## Manual Verification

Per the spec and project convention (GTK wiring is not unit-tested):

- Launch `python3 -m openvpn_manager`. Add an SSH tunnel under pc → right-click → Connect. A SOCKS proxy appears on a free port; the dot turns green; `ps` shows the ssh process.
- Add a second SSH tunnel under the first → Connect → the child's argv routes through the parent's port (`ps -ef | grep ssh` shows `ProxyCommand=nc -X 5 -x 127.0.0.1:<parent> %h %p`); the child reaches hosts only visible behind the parent.
- Add an OpenVPN tunnel bound to an imported profile → Connect → `systemctl start openvpn-client@<profile>` runs; dot turns green; no duplicate auto node appears in the tree.
- Disconnect a parent → its descendants flip to disconnected in the same refresh.
- Right-click a WireGuard tunnel → Connect is disabled with the "not supported" note.
- Kill the ssh process manually → the dot flips to red within a second (tick repaint).
