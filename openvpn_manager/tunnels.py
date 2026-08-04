"""Pure helpers for connecting manual tunnels. No GTK imports.

All system reads/processes go through injectable seams so this module is
testable without touching the real system.
"""


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
