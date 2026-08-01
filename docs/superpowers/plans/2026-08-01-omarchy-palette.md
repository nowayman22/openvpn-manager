# Omarchy Color Palette Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the OpenVPN Manager UI use the active Omarchy theme's color palette — reading `colors.toml` at launch, mapping it onto libadwaita named colors plus the sparkline, and live-updating when the theme changes.

**Architecture:** A pure module `openvpn_manager/palette.py` parses the active theme's `colors.toml` (`~/.config/omarchy/current/theme/colors.toml` — guaranteed present by `omarchy theme set`) into RGB float tuples, renders a GTK CSS string that overrides libadwaita's `accent`/`success`/`warning`/`error`/`destructive` named colors, and returns the two sparkline line colors. `app.py` loads the CSS via a `Gtk.CssProvider` at `STYLE_PROVIDER_PRIORITY_APPLICATION` and passes the line colors into `Sparkline`. A `Gio.FileMonitor` on `~/.config/omarchy/current/` (the directory that survives `omarchy theme set`'s `rm -rf` + `mv` swap) debounce-reloads the palette and re-applies it live. Any failure to read the palette keeps the last good colors.

**Tech Stack:** Python 3.11+ (`tomllib` stdlib), GTK4 / libadwaita 1.9.2, pytest via `.venv/bin/python -m pytest`.

## Global Constraints

- `requires-python = ">=3.11"` — use stdlib `tomllib`, add no dependencies.
- The app only **reads** `~/.config/omarchy/current/theme/colors.toml`. Never modify anything under `~/.local/share/omarchy/` or `~/.config/omarchy/`.
- Respect the system GTK color-scheme preference. Do not force dark/light mode; only swap semantic colors.
- Live-follow: monitor `~/.config/omarchy/current/` and re-apply the palette when the theme changes; keep the last good palette on any failure.
- Graceful fallback: missing/unreadable/malformed palette → current default colors, no crash.
- Semantic mapping is fixed per the stable `colors.toml` schema: `accent`→accent, `color1`→error/destructive, `color2`→success, `color3`→warning, `selection_background`→sparkline up line.
- Tests run with `.venv/bin/python -m pytest -q` (currently 42 passing). GTK widgets are verified manually (existing project convention); the palette module is unit-tested.

---

### Task 1: Palette module

**Files:**
- Create: `openvpn_manager/palette.py`
- Create: `tests/test_palette.py`

**Interfaces:**
- Produces:
  - `hex_to_rgba(value) -> tuple[float, float, float] | None` — parses `#RRGGBB` into 0..1 float RGB; `None` on malformed input.
  - `load_palette(path=None) -> dict | None` — reads a `colors.toml` (defaults to `CURRENT_THEME_PATH`) and returns `{"accent": rgb, "error": rgb?, "success": rgb?, "warning": rgb?, "up": rgb?}`; `None` when the file is missing, unreadable, or has no usable `accent`. Malformed individual values are skipped.
  - `palette_to_css(palette) -> str` — GTK CSS overriding libadwaita named colors.
  - `sparkline_colors(palette) -> tuple[tuple[float, float, float], tuple[float, float, float]]` — `(down_rgb, up_rgb)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_palette.py`:

```python
import textwrap

from openvpn_manager.palette import (
    hex_to_rgba,
    load_palette,
    palette_to_css,
    sparkline_colors,
)

PALETTE_TOML = textwrap.dedent("""
    accent = "#7daea3"
    cursor = "#bdae93"
    foreground = "#d4be98"
    background = "#282828"
    selection_foreground = "#ebdbb2"
    selection_background = "#d65d0e"

    color0 = "#3c3836"
    color1 = "#ea6962"
    color2 = "#a9b665"
    color3 = "#d8a657"
    color4 = "#7daea3"
    color5 = "#d3869b"
    color6 = "#89b482"
    color7 = "#d4be98"
""")


def rgb(hex_value):
    return tuple(int(hex_value[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def test_hex_to_rgba():
    assert hex_to_rgba("#7daea3") == rgb("#7daea3")


def test_hex_to_rgba_malformed():
    assert hex_to_rgba("#12345") is None
    assert hex_to_rgba("7daea3") is None
    assert hex_to_rgba("#gggggg") is None
    assert hex_to_rgba(None) is None


def test_load_palette_full(tmp_path):
    colors = tmp_path / "colors.toml"
    colors.write_text(PALETTE_TOML)
    palette = load_palette(colors)
    assert palette["accent"] == rgb("#7daea3")
    assert palette["error"] == rgb("#ea6962")
    assert palette["success"] == rgb("#a9b665")
    assert palette["warning"] == rgb("#d8a657")
    assert palette["up"] == rgb("#d65d0e")


def test_load_palette_missing(tmp_path):
    assert load_palette(tmp_path / "nope.toml") is None


def test_load_palette_bad_value_skipped(tmp_path):
    colors = tmp_path / "colors.toml"
    colors.write_text(
        'accent = "#7daea3"\ncolor2 = "not-a-color"\ncolor3 = "#d8a657"\n')
    palette = load_palette(colors)
    assert palette["accent"] == rgb("#7daea3")
    assert "success" not in palette
    assert palette["warning"] == rgb("#d8a657")


def test_load_palette_no_accent(tmp_path):
    colors = tmp_path / "colors.toml"
    colors.write_text('color1 = "#ea6962"\n')
    assert load_palette(colors) is None


def test_palette_to_css():
    palette = {
        "accent": rgb("#7daea3"),
        "error": rgb("#ea6962"),
        "success": rgb("#a9b665"),
        "warning": rgb("#d8a657"),
        "up": rgb("#d65d0e"),
    }
    css = palette_to_css(palette)
    assert "@define-color accent_bg_color #7daea3;" in css
    assert "@define-color success_bg_color #a9b665;" in css
    assert "@define-color error_bg_color #ea6962;" in css
    assert "@define-color destructive_bg_color #ea6962;" in css
    assert "@define-color warning_bg_color #d8a657;" in css


def test_sparkline_colors():
    palette = {"accent": rgb("#7daea3"), "up": rgb("#d65d0e")}
    assert sparkline_colors(palette) == (rgb("#7daea3"), rgb("#d65d0e"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_palette.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'openvpn_manager.palette'`

- [ ] **Step 3: Write the implementation**

Create `openvpn_manager/palette.py`:

```python
"""Read the active Omarchy theme palette and map it onto GTK colors."""

import tomllib
from pathlib import Path

CURRENT_THEME_PATH = Path.home() / ".config" / "omarchy" / "current" / "theme" / "colors.toml"


def hex_to_rgba(value):
    """Parse a #RRGGBB string into a (r, g, b) tuple of 0..1 floats.

    Returns None for anything that isn't a well-formed 6-digit hex color.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.startswith("#") or len(text) != 7:
        return None
    try:
        return tuple(int(text[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    except ValueError:
        return None


def load_palette(path=None):
    """Load the active Omarchy theme palette.

    Returns a dict mapping semantic names to (r, g, b) float tuples:
    ``accent``, and optionally ``error``, ``success``, ``warning``, ``up``.
    Returns None when the file is missing, unreadable, or has no usable
    accent. Malformed individual values are skipped.
    """
    if path is None:
        path = CURRENT_THEME_PATH
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    def pick(key):
        value = data.get(key)
        if not isinstance(value, str):
            return None
        return hex_to_rgba(value)

    accent = pick("accent")
    if accent is None:
        return None
    palette = {"accent": accent}
    for key, name in (("color1", "error"),
                      ("color2", "success"),
                      ("color3", "warning"),
                      ("selection_background", "up")):
        color = pick(key)
        if color is not None:
            palette[name] = color
    return palette


def palette_to_css(palette):
    """Render GTK CSS overriding libadwaita's named colors."""

    def hex(rgb):
        return "#%02x%02x%02x" % tuple(round(c * 255) for c in rgb)

    lines = [f"@define-color accent_bg_color {hex(palette['accent'])};",
             f"@define-color accent_color {hex(palette['accent'])};"]
    for name in ("error", "success", "warning"):
        if name not in palette:
            continue
        color = hex(palette[name])
        lines.append(f"@define-color {name}_bg_color {color};")
        lines.append(f"@define-color {name}_color {color};")
        if name == "error":
            lines.append(f"@define-color destructive_bg_color {color};")
            lines.append(f"@define-color destructive_color {color};")
    return "\n".join(lines)


def sparkline_colors(palette):
    """Return (down_rgb, up_rgb) for the sparkline from a palette."""
    return palette["accent"], palette.get("up", palette["accent"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_palette.py -q`
Expected: `8 passed`

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `42 passed` (34 existing + 8 new)

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/palette.py tests/test_palette.py
git commit -m "feat: add Omarchy palette loader and GTK CSS mapping"
```

---

### Task 2: Sparkline palette colors

**Files:**
- Modify: `openvpn_manager/sparkline.py:13-47`

**Interfaces:**
- Consumes: `down_color`, `up_color` as optional `tuple[float, float, float]` args from `Task 1` (`sparkline_colors` return values).
- Produces: `Sparkline(down_color=None, up_color=None)` — line colors default to today's hardcoded values when not given, so unt-themed callers are unchanged.

- [ ] **Step 1: Modify the constructor to accept line colors**

In `openvpn_manager/sparkline.py`, change the constructor and `_draw`:

```python
class Sparkline(Gtk.DrawingArea):
    def __init__(self, down_color=None, up_color=None):
        super().__init__()
        self.set_content_height(120)
        self.set_hexpand(True)
        self._down = collections.deque(maxlen=MAX_SAMPLES)
        self._up = collections.deque(maxlen=MAX_SAMPLES)
        self._down_color = down_color or (0.20, 0.60, 0.86)
        self._up_color = up_color or (0.90, 0.49, 0.13)
        self.set_draw_func(self._draw)
```

And in `_draw`, replace the two hardcoded `_draw_series` calls:

```python
        self._draw_series(cr, self._down, width, height, peak, self._down_color)
        self._draw_series(cr, self._up, width, height, peak, self._up_color)
```

The `_draw_series` method is unchanged (it already takes an `rgb` tuple and calls `cr.set_source_rgb(*rgb)`).

- [ ] **Step 2: Verify the module imports and the suite passes**

Run: `.venv/bin/python -c "from openvpn_manager.sparkline import Sparkline; print('ok')"`
Expected: `ok`

Run: `.venv/bin/python -m pytest -q`
Expected: `42 passed`

- [ ] **Step 3: Commit**

```bash
git add openvpn_manager/sparkline.py
git commit -m "feat: allow sparkline line colors to be themed"
```

---

### Task 3: Wire the palette into the app window

**Files:**
- Modify: `openvpn_manager/app.py:7-16` (imports), `app.py:24-26` and `app.py:95` (init), `app.py:146` (new method).

**Interfaces:**
- Consumes: `load_palette`, `palette_to_css`, `sparkline_colors` from `Task 1`; `Sparkline(down_color=..., up_color=...)` from `Task 2`.

- [ ] **Step 1: Add the Gdk import and palette imports**

In `openvpn_manager/app.py`, replace lines 7-16 (the `gi.require_version` block and imports) with:

```python
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import vpn
from .format import human_bytes, human_duration, human_speed
from .palette import load_palette, palette_to_css, sparkline_colors
from .sparkline import Sparkline
from .stats import Sampler, detect_iface
```

- [ ] **Step 2: Load the palette at window init**

In `Window.__init__`, right after `self._iface = None` (line 27), insert:

```python
        self._palette = load_palette()
        self._spark_colors = (
            sparkline_colors(self._palette) if self._palette else (None, None))
        if self._palette is not None:
            self._apply_theme_css(self._palette)
```

- [ ] **Step 3: Pass line colors to the sparkline**

Change the `Sparkline()` construction (line 95) from:

```python
        self._spark = Sparkline()
```

to:

```python
        self._spark = Sparkline(
            down_color=self._spark_colors[0], up_color=self._spark_colors[1])
```

- [ ] **Step 4: Add the CSS application method**

After the `_speed_header` static method (line 145), add:

```python
    @staticmethod
    def _apply_theme_css(palette):
        try:
            provider = Gtk.CssProvider()
            provider.load_from_string(palette_to_css(palette))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except GLib.Error:
            pass  # keep system defaults if the CSS fails to load
```

- [ ] **Step 5: Verify the module imports and the suite passes**

Run: `.venv/bin/python -c "from openvpn_manager.app import Window; print('ok')"`
Expected: `ok`

Run: `.venv/bin/python -m pytest -q`
Expected: `42 passed`

- [ ] **Step 6: Manual verification on the Gruvbox system**

Run: `.venv/bin/python -m openvpn_manager`
Expected (existing project convention — widgets are exercised manually):
- Connect button, focus highlight, and suggested-action elements use the Gruvbox teal accent (`#7daea3`).
- Status line and status pill "success" states use Gruvbox green (`#a9b665`); error states use Gruvbox red (`#ea6962`).
- Disconnect (destructive-action) button is Gruvbox red.
- Sparkline down line is teal, up line is orange (`#d65d0e`).

Sanity check the fallback: temporarily rename `~/.config/omarchy/current/theme/colors.toml`, relaunch — the app uses its default libadwaita colors with no crash — then restore the file.

- [ ] **Step 7: Commit**

```bash
git add openvpn_manager/app.py
git commit -m "feat: theme the app window with the active Omarchy palette"
```

---

### Task 4: Live palette switching

**Files:**
- Modify: `openvpn_manager/palette.py:9-11` (add `THEME_DIR`)
- Modify: `openvpn_manager/sparkline.py:22` (add `set_colors`)
- Modify: `openvpn_manager/app.py` (monitor + reload wiring)

**Interfaces:**
- Consumes: `load_palette`, `palette_to_css`, `sparkline_colors` from `Task 1`; `Sparkline` from `Task 2`.
- Produces: `palette.THEME_DIR` (path to `~/.config/omarchy/current`); `Sparkline.set_colors(down_color, up_color)`.

`omarchy theme set` replaces `~/.config/omarchy/current/theme/` wholesale (`rm -rf` + `mv`), so neither the file nor the `theme` directory can be watched — the monitor must sit on `~/.config/omarchy/current/`, which survives the swap.

- [ ] **Step 1: Add `THEME_DIR` to the palette module**

In `openvpn_manager/palette.py`, replace the two path constants:

```python
THEME_DIR = Path.home() / ".config" / "omarchy" / "current"
CURRENT_THEME_PATH = THEME_DIR / "theme" / "colors.toml"
```

- [ ] **Step 2: Add `set_colors` to the sparkline**

In `openvpn_manager/sparkline.py`, after `push`, add:

```python
    def set_colors(self, down_color, up_color):
        self._down_color = down_color or self._down_color
        self._up_color = up_color or self._up_color
        self.queue_draw()
```

- [ ] **Step 3: Hold a reusable provider and track reload state in `app.py`**

In `Window.__init__`, replace the palette block added in Task 3 (after `self._iface = None`) with:

```python
        self._sampler = None
        self._iface = None
        self._provider = Gtk.CssProvider()
        self._reload_pending = False
        self._reload_retries = 5
        self._palette = load_palette()
        self._spark_colors = (
            sparkline_colors(self._palette) if self._palette else (None, None))
        if self._palette is not None:
            self._apply_theme_css(self._palette)
```

- [ ] **Step 4: Convert `_apply_theme_css` to an instance method that reuses the provider**

Replace the Task 3 `_apply_theme_css` method (currently `@staticmethod`) with:

```python
    def _apply_theme_css(self, palette):
        try:
            self._provider.load_from_string(palette_to_css(palette))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self._provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except GLib.Error:
            pass  # keep the last good colors if the CSS fails to load
```

- [ ] **Step 5: Add the monitor and reload handlers**

In `Window`, after `_apply_theme_css`, add:

```python
    def _start_theme_monitor(self):
        try:
            monitor = Gio.File.new_for_path(str(palette.THEME_DIR))
            self._monitor = monitor.monitor_directory(
                Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return
        self._monitor.connect("changed", self._on_theme_changed)

    def _on_theme_changed(self, *_args):
        if self._reload_pending:
            return
        self._reload_pending = True
        GLib.timeout_add(200, self._reload_theme)

    def _reload_theme(self):
        self._reload_pending = False
        palette = load_palette()
        if palette is None:
            if self._reload_retries > 0:
                self._reload_retries -= 1
                return True  # file may be mid-swap; retry briefly
            return False  # give up, keep the last good colors
        self._reload_retries = 5
        self._apply_theme_css(palette)
        self._spark.set_colors(*sparkline_colors(palette))
        return False
```

- [ ] **Step 6: Start the monitor at the end of `__init__`**

In `Window.__init__`, after `GLib.timeout_add(1000, self._tick)`, add:

```python
        self._start_theme_monitor()
```

- [ ] **Step 7: Verify imports and the suite**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); gi.require_version('Gdk','4.0'); from openvpn_manager.app import Window; print('ok')"`
Expected: `ok`

Run: `.venv/bin/python -m pytest -q`
Expected: `42 passed`

- [ ] **Step 8: Manual verification — live theme switch**

With the app already running (from Task 3 Step 6), run `omarchy theme set "Tokyo Night"` in a terminal. Expected within ~1 s: accent/success/error colors and the sparkline re-colour to Tokyo Night's palette, no restart needed. Then run `omarchy theme set Gruvbox` and confirm it switches back.

- [ ] **Step 9: Commit**

```bash
git add openvpn_manager/palette.py openvpn_manager/sparkline.py openvpn_manager/app.py
git commit -m "feat: live-follow Omarchy theme palette changes"
```
