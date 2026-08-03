from openvpn_manager import topology
from openvpn_manager.topology import Device, Segment, build_topology


def test_build_topology_shape():
    lan = Segment(subnet="192.168.1.0/24",
                  gateway=Device(kind="router", label="Router", ip="192.168.1.1"))
    topo = build_topology("myhost", ["192.168.1.101"], lan, [])
    assert topo.hostname == "myhost"
    assert topo.local_ips == ["192.168.1.101"]
    assert topo.lan is lan
    assert topo.tunnels == []


def test_build_topology_defaults():
    topo = build_topology("myhost")
    assert topo.local_ips == []
    assert topo.lan is None
    assert topo.tunnels == []


def test_device_defaults():
    dev = Device(kind="tunnel", label="work")
    assert dev.ip is None
    assert dev.mac is None
    assert dev.vendor is None
    assert dev.detail is None
