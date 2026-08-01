"""Read the active Omarchy theme palette and map it onto GTK colors."""

import tomllib
from pathlib import Path

THEME_DIR = Path.home() / ".config" / "omarchy" / "current"
CURRENT_THEME_PATH = THEME_DIR / "theme" / "colors.toml"


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
