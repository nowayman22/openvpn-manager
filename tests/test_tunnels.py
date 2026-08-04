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
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive",
                                has_nc=lambda: True)
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


def test_failed_node_can_be_reconnected():
    calls, popen = _fake_popen(poll_value=None)
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "inactive")
    topo = _simple_topo()
    dev = next(d for d in topo.devices if d.id == "manual:a")
    mgr.connect(dev, topo=topo, profiles=[])
    calls[0]._poll = 255  # ssh process dies
    assert mgr.status("manual:a")[0] == "failed"
    results = mgr.connect(dev, topo=topo, profiles=[])
    assert results[-1][1] is True
    assert len(calls) == 2  # a fresh process was spawned


def test_status_openvpn_activating_is_connecting():
    calls, popen = _fake_popen(poll_value=None)
    mgr = tunnels.TunnelManager(popen=popen, is_active=lambda p: "activating")
    ovpn = Device(id="manual:v", kind="tunnel", label="v", parent_id="pc",
                  manual=True, protocol="OpenVPN", profile="work")
    mgr.connect(ovpn, topo=_topo_with(ovpn), profiles=["work"])
    assert mgr.status("manual:v")[0] == "connecting"
