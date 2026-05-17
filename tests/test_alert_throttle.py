"""Tests para scripts/alert_throttle.py."""
from datetime import datetime, timedelta

import alert_throttle


def test_should_send_first_time(tmp_path):
    send, reason = alert_throttle.should_send(
        ["problema A"], status_dir=str(tmp_path)
    )
    assert send is True
    assert "first" in reason or "window_passed" in reason


def test_should_send_throttled_within_window(tmp_path):
    # Primera vez: manda
    send1, _ = alert_throttle.should_send(["problema A"], status_dir=str(tmp_path))
    # Segunda vez ya mismo: throttled
    send2, reason2 = alert_throttle.should_send(
        ["problema A"], status_dir=str(tmp_path), window_min=30
    )
    assert send1 is True
    assert send2 is False
    assert "throttled" in reason2


def test_should_send_after_window(tmp_path):
    now = datetime.now()
    alert_throttle.should_send(
        ["problema A"], status_dir=str(tmp_path), now=now - timedelta(hours=2)
    )
    send, _ = alert_throttle.should_send(
        ["problema A"], status_dir=str(tmp_path), window_min=30, now=now
    )
    assert send is True


def test_different_problems_not_throttled(tmp_path):
    alert_throttle.should_send(["problema A"], status_dir=str(tmp_path))
    send, _ = alert_throttle.should_send(["problema B"], status_dir=str(tmp_path))
    assert send is True


def test_problem_order_does_not_matter(tmp_path):
    alert_throttle.should_send(
        ["A", "B"], status_dir=str(tmp_path), window_min=30
    )
    send, _ = alert_throttle.should_send(
        ["B", "A"], status_dir=str(tmp_path), window_min=30
    )
    assert send is False  # mismo fingerprint, esta throttled


def test_empty_problems_does_not_send(tmp_path):
    send, reason = alert_throttle.should_send([], status_dir=str(tmp_path))
    assert send is False
    assert "sin_problemas" in reason


def test_gc_removes_old_entries(tmp_path):
    """Entries > 24h se borran al guardar nuevo estado."""
    now = datetime.now()
    # Vieja entrada
    alert_throttle.should_send(
        ["vieja"], status_dir=str(tmp_path), now=now - timedelta(hours=48)
    )
    # Nueva entrada (triggera GC)
    alert_throttle.should_send(["nueva"], status_dir=str(tmp_path), now=now)
    state = alert_throttle._load_state(str(tmp_path))
    fps = list(state.keys())
    # La vieja deberia haberse purgado, la nueva esta
    assert len(state) == 1
    new_fp = alert_throttle._fingerprint(["nueva"])
    assert new_fp in fps
