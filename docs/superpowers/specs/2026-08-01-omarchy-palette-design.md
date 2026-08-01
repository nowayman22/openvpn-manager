# OpenVPN Manager — Omarchy Color Palette

Date: 2026-08-01
Status: Approved

## Summary

Make the OpenVPN Manager UI use the Omarchy color palette. At launch the app
reads the active Omarchy theme's `colors.toml` and maps it onto libadwaita's
named color variables via an app-priority GTK stylesheet. The sparkline graph
gets its two line colors from the same source. If the palette cannot be read
(non-Omarchy system, missing file, malformed values), the app falls back to
its current default colors with no crash.

## Goals

- UI accent, success, warning, and error colors match the active Omarchy theme.
- Sparkline down/up line colors come from the palette instead of hardcoded RGB.
- Follows whichever theme the user has set on the system — stock or custom.
- Graceful fallback to current behavior when the palette is unavailable.
- Purely additive theming: no change to backend logic or existing tests.

## Non-Goals

- Changing window layout, widgets, or behavior (`app.py` structure stays).
- Forcing dark/light mode — the app continues to respect the system GTK
  color-scheme preference; only the semantic colors are swapped.
- Live reload when the user switches themes while the app is running
  (palette is read once at launch).
- Modifying anything under `~/.local/share/omarchy/` or `~/.config/omarchy/`.

## Palette Source

Read `~/.config/omarchy/current/theme/colors.toml`. This is the canonical,
already-resolved palette for the active theme: `omarchy theme set` guarantees
a `colors.toml` there (generating it from the theme's `alacritty.toml` when
missing), and it covers both stock themes and user-customized themes.

`colors.toml` is a flat TOML file with hex color values (`#RRGGBB`). Keys used
by the mapping:

- `accent` — the theme accent.
- `selection_background` — bright orange in Gruvbox (sparkline up line).
- `color1` — red.
- `color2` — green.
- `color3` — yellow.

The mapping is fixed per this stable schema, so it applies to any Omarchy
theme, not just Gruvbox.

## Semantic Mapping

| Element | colors.toml key | Gruvbox value | Libadwaita variable(s) |
|---------|----------------|---------------|------------------------|
| Accent | `accent` | `#7daea3` | `accent_bg_color`, `accent_color` |
| Success | `color2` | `#a9b665` | `success_bg_color`, `success_color` |
| Warning | `color3` | `#d8a657` | `warning_bg_color`, `warning_color` |
| Error / destructive | `color1` | `#ea6962` | `error_bg_color`, `error_color`, `destructive_bg_color`, `destructive_color` |
| Sparkline down | `accent` | `#7daea3` | n/a (cairo) |
| Sparkline up | `selection_background` | `#d65d0e` | n/a (cairo) |

Overriding these variables re-colors the existing semantic classes already in
use: `suggested-action` (Connect button), `destructive-action` (Disconnect
button), `success`/`error` (status line and status pill), and focus
highlights. Libadwaita backgrounds remain the system defaults per the
respect-system-pref decision, so no `@media (prefers-color-scheme: ...)`
variants are needed.

## Module: `openvpn_manager/palette.py`

Pure, unit-testable, no Gtk dependency.

- `CURRENT_THEME_PATH` — path to the active theme's `colors.toml`
  (`Path.home() / ".config" / "omarchy" / "current" / "theme" / "colors.toml"`).
- `hex_to_rgba(value) -> tuple[float, float, float] | None` — parses `#RRGGBB`
  into 0..1 float RGB. Returns `None` on malformed input.
- `load_palette(path=CURRENT_THEME_PATH) -> dict | None` — reads the TOML,
  returns a dict of the keys above mapped to RGB float tuples, or `None` when
  the file is missing, unreadable, or yields no usable colors. Malformed
  individual values are skipped rather than failing the whole load.
- `palette_to_css(palette) -> str` — renders the GTK CSS
  (`@define-color accent_bg_color #7daea3;` etc. for both `_bg_color` and
  derived `_color` variables listed in the mapping).
- `sparkline_colors(palette) -> tuple[tuple[float, float, float], tuple[float, float, float]]`
  — returns `(down_rgb, up_rgb)` from the mapping.

Uses `tomllib` (Python 3.11+, already `requires-python = ">=3.11"`).

## Changes

### `sparkline.py`

Constructor accepts optional `down_color` and `up_color` (RGB float tuples).
Defaults to today's hardcoded values when not provided, so callers that don't
theme are unchanged.

### `app.py`

- Add `gi.require_version("Gdk", "4.0")` and import `Gdk`.
- At window init, call `load_palette()`:
  - If `None` — proceed exactly as today.
  - Otherwise: `palette_to_css(palette)` → load via `Gtk.CssProvider` +
    `Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)`; pass the two colors from
    `sparkline_colors(palette)` into `Sparkline(down_color=..., up_color=...)`.

## Error Handling

- Missing/unreadable `colors.toml` → `load_palette` returns `None` → defaults.
- Malformed hex → skipped; if no color parses, `None` → defaults.
- CSS load failure → caught; defaults remain in effect.

## Testing

New `tests/test_palette.py` in the existing plain-pytest style:

- `hex_to_rgba`: valid, short, malformed, empty, wrong-format inputs.
- `load_palette`: temp `colors.toml` with a full palette; missing file; a file
  with one bad value (others still parse); a file with no usable colors.
- `palette_to_css`: contains the expected `@define-color` lines.
- `sparkline_colors`: correct `(down, up)` tuples from a sample palette.

Existing pytest suite must still pass. Manual check: launch the app on this
Gruvbox system and confirm the Connect button, status pill, status line, and
sparkline use the Gruvbox palette.

## Open Questions

None.
