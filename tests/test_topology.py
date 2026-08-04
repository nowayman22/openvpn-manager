import subprocess
import textwrap
import tomllib

from openvpn_manager import topology
from openvpn_manager.topology import Device, Edge, Segment, Topology, build_topology


ROUTE = textwrap.dedent("""\
    Iface	Destination	Gateway 	Flags	RefCnt	Use	Metric	Mask		MTU	Window	IRTT
    wlan0	00000000	0101A8C0	0003	0	0	600	00000000	0	0	0
    wlan0	0001A8C0	00000000	0001	0	0	600	00FFFFFF	0	0	0
""").strip()


def test_build_topology_shape():
    root = Device(id="pc", kind="pc", label="myhost")
    lan_dev = Device(id="lan:192.168.1.1", kind="router", label="Router",
                     ip="192.168.1.1", parent_id="pc")
    topo = build_topology("myhost", ["192.168.1.101"], root=root,
                          devices=[root, lan_dev],
                          edges=[Edge(source_id="pc", target_id="lan:192.168.1.1", style="solid")])
    assert topo.hostname == "myhost"
    assert topo.local_ips == ["192.168.1.101"]
    assert topo.root is root
    assert len(topo.devices) == 2
    assert len(topo.edges) == 1


def test_build_topology_defaults():
    topo = build_topology("myhost")
    assert topo.local_ips == []
    assert topo.lan is None
    assert topo.tunnels == []
    assert topo.root is None
    assert topo.devices == []
    assert topo.edges == []


def test_device_defaults():
    dev = Device(id="tunnel:work", kind="tunnel", label="work")
    assert dev.ip is None
    assert dev.mac is None
    assert dev.vendor is None
    assert dev.detail is None
    assert dev.parent_id is None
    assert dev.manual is False
    assert dev.protocol is None


def test_device_id_default():
    dev = Device(kind="tunnel", label="work")
    # id defaults to kind:label when not given
    assert dev.id == "tunnel:work"


def test_vendor_known(monkeypatch):
    monkeypatch.setattr(topology, "_oui_cache", {"AA:BB:CC": "Acme"})
    assert topology.vendor("aa:bb:cc:dd:ee:ff") == "Acme"


def test_vendor_unknown(monkeypatch):
    monkeypatch.setattr(topology, "_oui_cache", {"AA:BB:CC": "Acme"})
    assert topology.vendor("00:00:00:00:00:01") is None


def test_vendor_injects_oui_text():
    text = "AA:BB:CC\tAcme Widgets\n"
    assert topology.vendor("aa:bb:cc:dd:ee:ff", oui_text=text) == "Acme Widgets"


def test_vendor_bad_mac():
    assert topology.vendor(None) is None
    assert topology.vendor("") is None
    assert topology.vendor("nope") is None


def test_vendor_fallback_map(monkeypatch):
    monkeypatch.setattr(topology, "_oui_cache", dict(topology._OUI_FALLBACK))
    assert topology.vendor("B8:27:EB:12:34:56") == "Raspberry Pi"


def test_gateway_and_subnet():
    gw, cidr = topology.gateway_and_subnet(ROUTE)
    assert gw == "192.168.1.1"
    assert cidr == "192.168.1.0/24"


def test_gateway_and_subnet_no_default():
    assert topology.gateway_and_subnet("") == (None, None)


def test_gateway_and_subnet_ignores_gateway_host_route():
    # A /32 host route to the gateway must not replace the /24 LAN subnet.
    routes = ROUTE + "\n" + textwrap.dedent("""\
        wlan0	0101A8C0	00000000	0005	0	0	600	FFFFFFFF	0	0	0
    """)
    gw, cidr = topology.gateway_and_subnet(routes)
    assert gw == "192.168.1.1"
    assert cidr == "192.168.1.0/24"


def test_gateway_and_subnet_prefers_physical_default():
    # A VPN default route (lower metric via tun) must not hide the LAN gateway.
    routes = textwrap.dedent("""\
        tun2	00000000	39C0660A	0003	0	0	0	00000080	0	0	0
        wlan0	00000000	0101A8C0	0003	0	0	600	00000000	0	0	0
        wlan0	0001A8C0	00000000	0001	0	0	600	00FFFFFF	0	0	0
    """).strip()
    gw, cidr = topology.gateway_and_subnet(routes)
    assert gw == "192.168.1.1"
    assert cidr == "192.168.1.0/24"


ARP = textwrap.dedent("""\
    IP address       HW type     Flags       HW address            Mask     Device
    192.168.1.1      0x1         0x2         e8:9f:80:8d:a0:c1     *        wlan0
    192.168.1.211    0x1         0x2         e0:01:c7:f7:df:74     *        wlan0
    192.168.1.240    0x1         0x2         00:00:00:00:00:00     *        wlan0
""")

NDISC = textwrap.dedent("""\
    fe80::ea9f:80ff:fe8d:9749 dev wlan0 lladdr e8:9f:80:8d:97:49 router STALE
    fe80::1 dev wlan0 lladdr 11:22:33:44:55:66 STALE
""")


def test_neighbors_parses_arp_and_ndisc():
    devices = topology.neighbors(ARP, NDISC)
    by_ip = {d.ip: d for d in devices}
    assert "192.168.1.1" in by_ip
    assert "192.168.1.211" in by_ip
    assert "192.168.1.240" not in by_ip  # incomplete MAC is skipped
    assert by_ip["192.168.1.211"].mac == "e0:01:c7:f7:df:74"


def test_neighbors_excludes_own_ips():
    devices = topology.neighbors(ARP, NDISC, own_ips=("192.168.1.211",))
    assert all(d.ip != "192.168.1.211" for d in devices)


def test_build_segment_splits_gateway():
    devices = topology.neighbors(ARP, NDISC)
    seg = topology.build_segment("192.168.1.1", "192.168.1.0/24", devices)
    assert seg.subnet == "192.168.1.0/24"
    assert seg.gateway.ip == "192.168.1.1"
    assert seg.gateway.kind == "router"
    assert all(d.ip != "192.168.1.1" for d in seg.devices)


def test_build_segment_no_gateway():
    assert topology.build_segment(None, None, []) is None


ADDR = textwrap.dedent("""\
    1: lo    inet 127.0.0.1/8 scope host lo
    4: wlan0    inet 192.168.1.101/24 metric 600 brd 192.168.1.255 scope global dynamic wlan0
    15: tun0    inet 10.8.0.2/24 scope global tun0
""")


def test_local_ips_skips_lo_and_tunnels():
    assert topology.local_ips(ADDR) == ["192.168.1.101"]


def test_tun_ips():
    assert topology.tun_ips(ADDR) == ["10.8.0.2"]


def test_connected_tunnels_active(tmp_path):
    (tmp_path / "work.conf").write_text("remote vpn.example.com 1194\nproto udp\n")
    (tmp_path / "home.conf").write_text("remote home.example.net 1194\n")

    def fake_runner(argv):
        unit = argv[-1]
        state = "active" if unit.endswith("@work") else "inactive"
        return subprocess.CompletedProcess(argv, 0, stdout=state + "\n", stderr="")

    tunnels = topology.connected_tunnels(
        ["home", "work"], tunnel_ips=["10.8.0.2"],
        runner=fake_runner, client_dir=str(tmp_path))
    assert len(tunnels) == 1
    assert tunnels[0].kind == "tunnel"
    assert tunnels[0].label == "work"
    assert tunnels[0].ip == "10.8.0.2"
    assert tunnels[0].detail == "vpn.example.com"


def test_connected_tunnels_none_active(tmp_path):
    def fake_runner(argv):
        return subprocess.CompletedProcess(argv, 0, stdout="inactive\n", stderr="")

    assert topology.connected_tunnels(["work"], runner=fake_runner,
                                      client_dir=str(tmp_path)) == []


def test_sweep_argv():
    argv = topology.sweep_argv("192.168.1.0/24")
    assert argv[0] == "pkexec"
    assert argv[1].endswith("helper.sh")
    assert argv[2] == "scan"
    assert argv[3] == "192.168.1.0/24"


def test_build_tree_topology_shape():
    lan = Segment(subnet="192.168.1.0/24",
                  gateway=Device(kind="router", label="Router", ip="192.168.1.1"),
                  devices=[Device(kind="device", label="Pi", ip="192.168.1.211")])
    auto_tun = [Device(kind="tunnel", label="home", ip="10.8.0.2", detail="vpn.example.com")]
    topo = topology.build_tree_topology("myhost", ["192.168.1.101"], lan, auto_tun)
    assert topo.root.id == "pc"
    assert topo.root.kind == "pc"
    assert len(topo.devices) == 4  # pc + router + Pi + tunnel
    ids = {d.id for d in topo.devices}
    assert "lan:192.168.1.1" in ids
    assert "lan:192.168.1.211" in ids
    assert "tun:home" in ids
    # PC -> router (solid), PC -> tunnel (dashed), router -> Pi (solid)
    styles = {(e.source_id, e.target_id): e.style for e in topo.edges}
    assert styles[("pc", "lan:192.168.1.1")] == "solid"
    assert styles[("pc", "tun:home")] == "dashed"
    assert styles[("lan:192.168.1.1", "lan:192.168.1.211")] == "solid"


def test_build_tree_topology_no_lan():
    topo = topology.build_tree_topology("myhost", ["192.168.1.101"], None, [])
    assert topo.root.id == "pc"
    assert len(topo.devices) == 1  # just pc
    assert topo.edges == []


def test_build_tree_topology_manual_tunnels():
    manual = [Device(kind="tunnel", label="relay", id="manual:relay",
                     parent_id="tun:home", manual=True, protocol="SSH")]
    lan = Segment(subnet="192.168.1.0/24",
                  gateway=Device(kind="router", label="Router", ip="192.168.1.1"))
    auto_tun = [Device(kind="tunnel", label="home", ip="10.8.0.2", detail="vpn.example.com")]
    topo = topology.build_tree_topology("myhost", [], lan, auto_tun, manual)
    ids = {d.id for d in topo.devices}
    assert "manual:relay" in ids
    styles = {(e.source_id, e.target_id): e.style for e in topo.edges}
    assert styles[("tun:home", "manual:relay")] == "dashed"


def test_chain_tunnels():
    tunnels = [
        Device(kind="tunnel", label="outer", ip="10.8.0.2", detail="vpn.example.com"),
        Device(kind="tunnel", label="inner", ip="10.5.0.1", detail="10.8.0.2"),
    ]
    pairs = topology.chain_tunnels(tunnels)
    assert len(pairs) == 1
    assert pairs[0] == ("tunnel:outer", "tunnel:inner")


def test_chain_tunnels_no_match():
    tunnels = [
        Device(kind="tunnel", label="a", ip="10.8.0.2"),
        Device(kind="tunnel", label="b", ip="10.9.0.1"),
    ]
    assert topology.chain_tunnels(tunnels) == []


def test_save_and_load_manual_tunnels(tmp_path):
    path = tmp_path / "topology.toml"
    devs = [
        Device(id="manual:t1", kind="tunnel", label="relay", parent_id="pc",
               manual=True, protocol="WireGuard",
               detail="relay.example.com"),
        Device(id="manual:t2", kind="tunnel", label="inner", parent_id="tun:home",
               manual=True, protocol="SSH",
               detail="10.8.0.5"),
    ]
    topology.save_manual_tunnels(devs, path)
    loaded = topology.load_manual_tunnels(path)
    assert len(loaded) == 2
    assert loaded[0].id == "manual:t1"
    assert loaded[0].parent_id == "pc"
    assert loaded[0].protocol == "WireGuard"
    assert loaded[0].manual is True
    assert loaded[1].id == "manual:t2"
    assert loaded[1].parent_id == "tun:home"
    assert loaded[1].protocol == "SSH"


def test_load_manual_tunnels_missing_file(tmp_path):
    assert topology.load_manual_tunnels(tmp_path / "nope.toml") == []


def test_load_manual_tunnels_malformed(tmp_path):
    path = tmp_path / "topology.toml"
    path.write_text("not valid toml at all [[[")
    assert topology.load_manual_tunnels(path) == []


def test_compute_layout_positions():
    root = Device(id="pc", kind="pc", label="host")
    child = Device(id="tun:a", kind="tunnel", label="a", parent_id="pc")
    topo = Topology(hostname="host", root=root,
                    devices=[root, child],
                    edges=[Edge(source_id="pc", target_id="tun:a", style="dashed")])
    boxes = topology.compute_layout(topo, col_width=140, node_pad=12, level_pad=60)
    by_id = {b.device_id: b for b in boxes}
    assert len(boxes) == 2
    assert by_id["pc"].x == 0
    assert by_id["tun:a"].x == 200  # col_width + level_pad
    # Both nodes have valid, non-negative y
    assert by_id["pc"].y >= 0
    assert by_id["tun:a"].y >= 0


def test_compute_layout_sibling_spacing():
    root = Device(id="pc", kind="pc", label="host")
    a = Device(id="tun:a", kind="tunnel", label="a", parent_id="pc")
    b = Device(id="tun:b", kind="tunnel", label="b", parent_id="pc")
    topo = Topology(hostname="host", root=root,
                    devices=[root, a, b],
                    edges=[Edge(source_id="pc", target_id="tun:a", style="dashed"),
                           Edge(source_id="pc", target_id="tun:b", style="dashed")])
    boxes = topology.compute_layout(topo)
    by_id = {b.device_id: b for b in boxes}
    # siblings have same x, different y
    assert by_id["tun:a"].x == by_id["tun:b"].x
    assert abs(by_id["tun:a"].y - by_id["tun:b"].y) > 0


def test_delete_manual_tunnel_reparents_children():
    tunnels = [
        Device(id="manual:a", kind="tunnel", label="a", parent_id="pc", manual=True),
        Device(id="manual:b", kind="tunnel", label="b", parent_id="manual:a", manual=True),
        Device(id="manual:c", kind="tunnel", label="c", parent_id="manual:a", manual=True),
    ]
    out = topology.delete_manual_tunnel(tunnels, "manual:a")
    assert [t.id for t in out] == ["manual:b", "manual:c"]
    assert all(t.parent_id == "pc" for t in out)


def test_delete_manual_tunnel_reparents_to_removed_parent():
    tunnels = [
        Device(id="manual:a", kind="tunnel", label="a", parent_id="tun:home", manual=True),
        Device(id="manual:b", kind="tunnel", label="b", parent_id="manual:a", manual=True),
    ]
    out = topology.delete_manual_tunnel(tunnels, "manual:a")
    assert len(out) == 1
    assert out[0].id == "manual:b"
    assert out[0].parent_id == "tun:home"


def test_delete_manual_tunnel_missing_id():
    tunnels = [Device(id="manual:a", kind="tunnel", label="a", manual=True)]
    out = topology.delete_manual_tunnel(tunnels, "manual:nope")
    assert out == tunnels


def test_update_manual_tunnel():
    tunnels = [Device(id="manual:a", kind="tunnel", label="a", parent_id="pc",
                      manual=True, protocol="SSH", detail="old.example.com")]
    out = topology.update_manual_tunnel(
        tunnels, "manual:a", label="b", parent_id="tun:home",
        remote="new.example.com", protocol="WireGuard")
    assert len(out) == 1
    assert out[0].id == "manual:a"
    assert out[0].label == "b"
    assert out[0].parent_id == "tun:home"
    assert out[0].detail == "new.example.com"
    assert out[0].protocol == "WireGuard"
    assert out[0].manual is True


def test_update_manual_tunnel_missing_id():
    tunnels = [Device(id="manual:a", kind="tunnel", label="a", manual=True)]
    out = topology.update_manual_tunnel(
        tunnels, "manual:nope", label="x", parent_id="pc",
        remote="r.example.com", protocol="SSH")
    assert out == tunnels


def test_descendant_ids_multilevel():
    tunnels = [
        Device(id="pc", kind="pc", label="host"),
        Device(id="manual:a", kind="tunnel", label="a", parent_id="pc", manual=True),
        Device(id="manual:b", kind="tunnel", label="b", parent_id="manual:a", manual=True),
        Device(id="manual:c", kind="tunnel", label="c", parent_id="manual:b", manual=True),
        Device(id="manual:d", kind="tunnel", label="d", parent_id="pc", manual=True),
    ]
    assert topology.descendant_ids(tunnels, "manual:a") == {"manual:b", "manual:c"}
    assert topology.descendant_ids(tunnels, "manual:d") == set()


def test_descendant_ids_leaf():
    tunnels = [Device(id="manual:a", kind="tunnel", label="a", parent_id="pc", manual=True)]
    assert topology.descendant_ids(tunnels, "manual:a") == set()


def test_descendant_ids_cycle_terminates():
    tunnels = [
        Device(id="manual:a", kind="tunnel", label="a", parent_id="manual:b", manual=True),
        Device(id="manual:b", kind="tunnel", label="b", parent_id="manual:a", manual=True),
    ]
    assert topology.descendant_ids(tunnels, "manual:a") == {"manual:b"}


class _FakeSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _tcp_raises(msg):
    def raiser(*_args, **_kwargs):
        raise OSError(msg)
    return raiser


def _ping_runner(retcode, stdout="", stderr=""):
    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], retcode,
                                           stdout=stdout, stderr=stderr)
    return runner


def test_test_tunnel_tcp_connect_success():
    sock = _FakeSocket()
    status, msg = topology.test_tunnel(
        "10.8.0.9", "SSH", connect=lambda *a, **k: sock)
    assert status == "ok"
    assert "TCP 10.8.0.9:22" in msg
    assert sock.closed


def test_test_tunnel_tcp_fails_ping_succeeds():
    status, msg = topology.test_tunnel(
        "10.8.0.9", "SSH", connect=_tcp_raises("timed out"),
        ping_runner=_ping_runner(0, stdout=(
            "64 bytes from 10.8.0.9: icmp_seq=1 ttl=64 time=0.3 ms\n"
            "64 bytes from 10.8.0.9: icmp_seq=2 ttl=64 time=0.3 ms\n")))
    assert status == "ok"
    assert "2/2" in msg


def test_test_tunnel_both_fail():
    status, msg = topology.test_tunnel(
        "10.8.0.9", "SSH", connect=_tcp_raises("timed out"),
        ping_runner=_ping_runner(1, stderr="ping: destination unreachable"))
    assert status == "fail"
    assert "timed out" in msg


def test_test_tunnel_no_port_uses_ping():
    status, msg = topology.test_tunnel(
        "10.8.0.9", None,
        ping_runner=_ping_runner(0, stdout=(
            "64 bytes from 10.8.0.9: icmp_seq=1 ttl=64 time=0.3 ms\n")))
    assert status == "ok"
    assert "ping" in msg


def test_test_tunnel_unknown_protocol_falls_back_to_ping():
    status, msg = topology.test_tunnel(
        "example.com", "Quic",
        ping_runner=_ping_runner(0, stdout=(
            "64 bytes from example.com: icmp_seq=1 ttl=50 time=1.0 ms\n")))
    assert status == "ok"
    assert "1/2" in msg


def test_test_tunnel_empty_remote():
    status, msg = topology.test_tunnel("", "SSH")
    assert status == "fail"
    assert "no remote" in msg
