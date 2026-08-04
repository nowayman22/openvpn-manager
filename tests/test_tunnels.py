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
