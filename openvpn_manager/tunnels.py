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
