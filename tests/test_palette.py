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
