import os

from openvpn_manager.stats import detect_iface, read_counters, Sampler


def _make_net(tmp_path, name, operstate, rx, tx):
    d = tmp_path / name
    (d / "statistics").mkdir(parents=True)
    (d / "operstate").write_text(operstate + "\n")
    (d / "statistics" / "rx_bytes").write_text(str(rx) + "\n")
    (d / "statistics" / "tx_bytes").write_text(str(tx) + "\n")


def test_detect_iface_finds_tun(tmp_path):
    _make_net(tmp_path, "enp0s31f6", "up", 0, 0)
    _make_net(tmp_path, "tun0", "up", 10, 20)
    assert detect_iface(str(tmp_path)) == "tun0"


def test_detect_iface_ignores_down_tun(tmp_path):
    _make_net(tmp_path, "tun0", "down", 0, 0)
    assert detect_iface(str(tmp_path)) is None


def test_detect_iface_none_when_no_tunnel(tmp_path):
    _make_net(tmp_path, "wlan0", "up", 0, 0)
    assert detect_iface(str(tmp_path)) is None


def test_read_counters(tmp_path):
    _make_net(tmp_path, "tun0", "up", 111, 222)
    assert read_counters("tun0", str(tmp_path)) == (111, 222)


def test_sampler_first_sample_is_zero():
    counters = iter([(1000, 2000)])
    clock = iter([100.0])
    s = Sampler("tun0", reader=lambda i, d=None: next(counters), clock=lambda: next(clock))
    out = s.sample()
    assert out == {"up_bps": 0.0, "down_bps": 0.0, "total_rx": 0, "total_tx": 0}


def test_sampler_computes_speed_and_totals():
    counters = iter([(1000, 2000), (1000 + 500, 2000 + 100)])
    clock = iter([100.0, 102.0])  # 2 second gap
    s = Sampler("tun0", reader=lambda i, d=None: next(counters), clock=lambda: next(clock))
    s.sample()
    out = s.sample()
    assert out["down_bps"] == 250.0  # 500 rx / 2s
    assert out["up_bps"] == 50.0     # 100 tx / 2s
    assert out["total_rx"] == 500
    assert out["total_tx"] == 100
