# OpenVPN Manager — UI Cleanup

Date: 2026-08-01
Status: Approved

## Summary

Restructure the OpenVPN Manager window from a flat stack of widgets into a
card-based dashboard, following the layout originally outlined in
`2026-07-29-openvpn-manager-design.md` (which the implementation had drifted
from). Pure UI refactor of `app.py`: no changes to backend behavior, modules,
or tests.

## Goals

- Structured card layout: a connection card and a usage card on a `Adw.Clamp`.
- Idiomatic libadwaita widgets: `Adw.ComboRow` for profile selection, `Adw.ActionRow`
  for connection info, `Adw.StatusPage` empty state, `Adw.CardGroup` cards.
- Usage card always visible (zeroed when idle), not hidden on disconnect.
- Fix two small UI inconsistencies discovered during the refactor.

## Non-Goals

- Changing backend logic (`vpn.py`, `stats.py`, `sparkline.py`, `format.py`).
- Adding new functionality beyond the empty-state import entry point.
- Changing tests.

## Layout

`Adw.Clamp` (max width ~460) on the window content, containing a vertical box
(spacing 12) with:

### Header (current structure, one styling addition)

- Title, status pill (`Connected` / `Connecting` / `Disconnected`), overflow
  menu (Import .ovpn). The pill additionally gets the `success` style class
  when connected so state reads at a glance.

### Card 1 — Connection (`Adw.CardGroup`)

- `Adw.ComboRow` "Profile" listing discovered profiles (replaces the current
  labeled dropdown in a `Gtk.Box`).
- Full-width Connect/Disconnect `Gtk.Button` below the row:
  - idle → `suggested-action`, label "Connect".
  - active → plain button, label "Disconnect".
  - activating → disabled, label "Connecting…" (fixes the misleading
    "Cancel" label that actually re-triggered connect on click).

### Card 2 — Usage (`Adw.CardGroup`, always visible)

- Two prominent current-speed values, Up and Down (`human_speed`), shown
  zeroed when idle.
- `Sparkline` graph, full card width.
- `Adw.ActionRow`s:
  - Session: `↑ <bytes>  ↓ <bytes>` (`human_bytes`).
  - Uptime (`human_duration`).
  - VPN IP, Remote, Protocol — one row each, value "—" when disconnected.

### Bottom

- Dim status line for persistent action/error feedback (unchanged role).
  Toasts remain for transient success messages.

### Empty state

When no profiles are discovered, the two cards are replaced by an
`Adw.StatusPage` ("No profiles imported") with a "Browse for .ovpn…" button
that opens the existing import flow. Removes the current disabled
"(no profiles)" dropdown placeholder.

## Window

Default size bumped from `420x560` to `460x640` so the always-visible usage
card and info rows fit comfortably.

## Data Flow

Unchanged. The 1-second `_tick` loop and `_update_usage` populate the widgets
exactly as today; only the widget tree and the selectors that update them
change. Widgets live on `self.` so existing handlers keep working.

## Testing

Widgets are exercised manually (existing convention). Existing pytest suite
is untouched and must still pass. Run the app under the test display to
verify each state: no profiles, disconnected, connecting, connected, and a
credential prompt.

## Open Questions

None.
