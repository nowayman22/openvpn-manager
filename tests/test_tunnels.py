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
