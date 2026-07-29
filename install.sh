#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(id -un)"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

echo "Installing OpenVPN Manager from $PROJECT_DIR"

mkdir -p "$APP_DIR" "$ICON_DIR"

install -m644 "$PROJECT_DIR/packaging/openvpn-manager.svg" "$ICON_DIR/openvpn-manager.svg"

sed "s|PROJECT_DIR|$PROJECT_DIR|g" "$PROJECT_DIR/packaging/openvpn-manager.desktop" \
    > "$APP_DIR/openvpn-manager.desktop"

echo "Installing polkit rule (needs root)..."
TMP_RULE="$(mktemp)"
sed "s|PROJECT_USER|$USER_NAME|g" "$PROJECT_DIR/packaging/50-openvpn-manager.rules" > "$TMP_RULE"
sudo install -m644 "$TMP_RULE" /etc/polkit-1/rules.d/50-openvpn-manager.rules
rm -f "$TMP_RULE"

# The client dir is 0750 openvpn:network; without this the app (running as you)
# cannot list profiles. An ACL is targeted and takes effect immediately.
CLIENT_DIR="/etc/openvpn/client"
echo "Granting read access to $CLIENT_DIR (needs root)..."
if command -v setfacl >/dev/null 2>&1; then
    sudo setfacl -m "u:$USER_NAME:rx" "$CLIENT_DIR"
else
    echo "  setfacl not found; adding you to the 'network' group instead (needs re-login)."
    sudo usermod -aG network "$USER_NAME"
fi

update-desktop-database "$APP_DIR" 2>/dev/null || true
gtk4-update-icon-cache -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "Done. Launch 'OpenVPN Manager' from your app menu."
