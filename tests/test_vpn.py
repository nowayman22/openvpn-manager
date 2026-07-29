import subprocess

from openvpn_manager import vpn


def test_unit_name():
    assert vpn.unit_name("work") == "openvpn-client@work"


def test_discover_profiles(tmp_path):
    (tmp_path / "work.conf").write_text("")
    (tmp_path / "home.conf").write_text("")
    (tmp_path / "notes.txt").write_text("")
    assert vpn.discover_profiles(str(tmp_path)) == ["home", "work"]


def test_discover_profiles_missing_dir(tmp_path):
    assert vpn.discover_profiles(str(tmp_path / "nope")) == []


def test_client_dir_readable_true(tmp_path):
    assert vpn.client_dir_readable(str(tmp_path)) is True


def test_client_dir_readable_missing(tmp_path):
    assert vpn.client_dir_readable(str(tmp_path / "nope")) is False


def test_client_dir_readable_no_permission(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        assert vpn.client_dir_readable(str(locked)) is False
    finally:
        locked.chmod(0o755)  # let pytest clean up


def test_connect_argv():
    assert vpn.connect_argv("work") == ["systemctl", "start", "openvpn-client@work"]


def test_disconnect_argv():
    assert vpn.disconnect_argv("work") == ["systemctl", "stop", "openvpn-client@work"]


def test_import_argv():
    argv = vpn.import_argv("/tmp/My VPN.ovpn", "/etc/openvpn/client")
    assert argv == ["pkexec", "cp", "/tmp/My VPN.ovpn", "/etc/openvpn/client/My VPN.conf"]


def test_is_active_uses_runner():
    calls = []

    def fake(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")

    assert vpn.is_active("work", runner=fake) == "active"
    assert calls[0] == ["systemctl", "is-active", "openvpn-client@work"]


def test_unit_property_uses_runner():
    def fake(argv):
        return subprocess.CompletedProcess(argv, 0, stdout="Sat 2026-07-29 10:00:00\n", stderr="")

    val = vpn.unit_property("work", "ActiveEnterTimestamp", runner=fake)
    assert val == "Sat 2026-07-29 10:00:00"


def test_parse_remote(tmp_path):
    conf = tmp_path / "work.conf"
    conf.write_text("client\nremote vpn.example.com 1194\nproto udp\n")
    assert vpn.parse_remote(str(conf)) == ("vpn.example.com", "udp")


def test_parse_remote_missing(tmp_path):
    assert vpn.parse_remote(str(tmp_path / "nope.conf")) == (None, None)
