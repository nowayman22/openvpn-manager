#!/usr/bin/env bash
# Privileged helper for OpenVPN Manager. Run via pkexec (as root).
#
#   helper.sh set-creds <profile>
#     Reads username (line 1) and password (line 2) from stdin, writes a
#     root-owned 0600 credentials file, and points the profile's
#     auth-user-pass directive at it.
#
# The password is passed on stdin (never argv) so it is not visible in ps.
set -euo pipefail

# Overridable only for testing; production always uses the system dir.
CLIENT_DIR="${OPENVPN_CLIENT_DIR:-/etc/openvpn/client}"
cmd="${1:-}"

case "$cmd" in
    set-creds)
        profile="${2:?profile name required}"
        conf="$CLIENT_DIR/$profile.conf"
        creds="$CLIENT_DIR/$profile.creds"
        [ -f "$conf" ] || { echo "no such profile: $conf" >&2; exit 1; }

        IFS= read -r username || true
        IFS= read -r password || true
        if [ -z "${username:-}" ] || [ -z "${password:-}" ]; then
            echo "username and password required on stdin" >&2
            exit 1
        fi

        umask 077
        printf '%s\n%s\n' "$username" "$password" > "$creds"
        chmod 600 "$creds"

        if grep -qE '^[[:space:]]*auth-user-pass' "$conf"; then
            sed -i "s|^[[:space:]]*auth-user-pass.*|auth-user-pass $creds|" "$conf"
        else
            printf 'auth-user-pass %s\n' "$creds" >> "$conf"
        fi
        ;;
    scan)
        # Populate the kernel ARP cache for a /24 subnet. The app re-reads
        # /proc/net/arp afterwards, so this only needs every live host to
        # answer an ARP request. Prefers arping; falls back to a broadcast
        # ping when arping is not installed.
        cidr="${2:?cidr required (e.g. 192.168.1.0/24)}"
        base="${cidr%/*}"
        prefix="${cidr#*/}"
        if [ "$prefix" != "24" ]; then
            echo "unsupported prefix: $prefix (only /24 is swept)" >&2
            exit 3
        fi
        net="${base%.*}"
        if command -v arping >/dev/null 2>&1; then
            seq 1 254 | xargs -P 32 -I{} \
                sh -c "arping -c 1 -w 1 '$net.{}' >/dev/null 2>&1" || true
        else
            # Hosts that answer the broadcast ping refresh their ARP entry.
            ping -b -c 2 -W 1 "${net}.255" >/dev/null 2>&1 || true
        fi
        ;;
    *)
        echo "unknown command: $cmd" >&2
        exit 2
        ;;
esac
