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
    *)
        echo "unknown command: $cmd" >&2
        exit 2
        ;;
esac
