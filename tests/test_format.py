from openvpn_manager.format import human_bytes, human_speed, human_duration


def test_human_bytes_zero():
    assert human_bytes(0) == "0 B"


def test_human_bytes_kb():
    assert human_bytes(1536) == "1.5 KB"


def test_human_bytes_mb():
    assert human_bytes(1_572_864) == "1.5 MB"


def test_human_bytes_gb():
    assert human_bytes(1_610_612_736) == "1.5 GB"


def test_human_speed_kb():
    assert human_speed(1536.0) == "1.5 KB/s"


def test_human_speed_zero():
    assert human_speed(0.0) == "0 B/s"


def test_human_duration_seconds():
    assert human_duration(95) == "00:01:35"


def test_human_duration_hours():
    assert human_duration(3661) == "01:01:01"


def test_human_duration_zero():
    assert human_duration(0) == "00:00:00"
