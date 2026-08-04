"""Regression tests for the topology diagram's right-click handling.

These need PyGObject (gi), which the project venv does not install; skip
cleanly when it is missing so the rest of the suite still runs.
"""

import os

import pytest

# Arch's gtk4 package does not ship the GDK "headless" backend, so forcing it
# segfaults on launch. Only fall back to headless when no display is present.
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("GDK_BACKEND", "headless")

gi = pytest.importorskip("gi")

gi.require_version("Gtk", "4.0")  # noqa: E402
gi.require_version("Gdk", "4.0")  # noqa: E402
from gi.repository import Gdk, Gtk  # noqa: E402

from openvpn_manager.topology import Device, Edge, Topology  # noqa: E402
from openvpn_manager.topology_diagram import TopologyDiagram  # noqa: E402


def _click_gestures(diagram):
    model = diagram.observe_controllers()
    if hasattr(model, "get_n_items"):
        controllers = [model.get_item(i)
                       for i in range(model.get_n_items())]
    else:
        controllers = list(model)
    return [c for c in controllers if isinstance(c, Gtk.GestureClick)]


def test_click_gesture_listens_to_all_buttons():
    """Right-click must reach the diagram.

    GtkGestureSingle defaults the button property to the primary button (1),
    so a plain Gtk.GestureClick.new() swallows secondary-button presses and
    the context menu never opens. The diagram must set button 0 (any button).
    """
    diagram = TopologyDiagram()
    gestures = _click_gestures(diagram)
    assert gestures, "TopologyDiagram must own a click gesture"
    for gesture in gestures:
        assert gesture.get_button() == 0, (
            "click gesture must listen to any button (0), got "
            f"{gesture.get_button()}")


def test_right_click_forwards_device_to_context_menu():
    """A secondary-button press on a node invokes the context callback."""
    root = Device(id="pc", kind="pc", label="host")
    tunnel = Device(id="manual:abc", kind="tunnel", label="nested",
                    parent_id="pc", manual=True, detail="10.0.0.5")
    topo = Topology(hostname="host", root=root, devices=[root, tunnel],
                    edges=[Edge(source_id="pc", target_id=tunnel.id,
                                style="solid")])

    calls = []

    def on_context(dev, x, y):
        calls.append((dev, x, y))

    diagram = TopologyDiagram(on_context_menu=on_context)
    diagram.set_topology(topo)
    box = next(b for b in diagram._boxes if b.device_id == tunnel.id)
    cx = box.x + box.w / 2
    cy = box.y + box.h / 2

    class _SecondaryGesture:
        def get_current_button(self):
            return Gdk.BUTTON_SECONDARY

    diagram._on_click(_SecondaryGesture(), 1, cx, cy)

    assert calls == [(tunnel, cx, cy)]


def test_left_click_does_not_open_context_menu():
    """Primary-button presses must not trigger the context callback."""
    root = Device(id="pc", kind="pc", label="host")
    tunnel = Device(id="manual:abc", kind="tunnel", label="nested",
                    parent_id="pc", manual=True, detail="10.0.0.5")
    topo = Topology(hostname="host", root=root, devices=[root, tunnel],
                    edges=[Edge(source_id="pc", target_id=tunnel.id,
                                style="solid")])

    calls = []

    def on_context(dev, x, y):
        calls.append((dev, x, y))

    diagram = TopologyDiagram(on_context_menu=on_context)
    diagram.set_topology(topo)
    box = next(b for b in diagram._boxes if b.device_id == tunnel.id)

    class _PrimaryGesture:
        def get_current_button(self):
            return Gdk.BUTTON_PRIMARY

    diagram._on_click(_PrimaryGesture(), 1,
                      box.x + box.w / 2, box.y + box.h / 2)

    assert calls == []


def test_tunnel_dot_maps_connection_stages():
    from openvpn_manager.topology_diagram import tunnel_dot
    assert tunnel_dot("connected") == tunnel_dot("ok")
    assert tunnel_dot("connecting") == tunnel_dot("testing")
    assert tunnel_dot("failed") == tunnel_dot("fail")
    assert tunnel_dot("disconnected") is None
    assert tunnel_dot(None) is None
