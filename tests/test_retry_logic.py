import os
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

import should_retry

def test_should_retry_no_active_tenants():
    with patch("should_retry.load_active_tenants", return_value=[]):
        assert should_retry.main() == 1

def test_should_retry_no_metrics_file(tmp_path):
    with patch("should_retry.load_active_tenants", return_value=["alpha"]):
        with patch("should_retry.STATUS_DIR", str(tmp_path)):
            assert should_retry.main() == 1

def test_should_retry_needed_on_fail(tmp_path):
    ar_tz = timezone(timedelta(hours=-3))
    today_str = datetime.now(ar_tz).strftime("%Y-%m-%d")

    metrics_file = tmp_path / "metrics.jsonl"
    metrics_file.write_text(
        json.dumps({"ts": f"{today_str}T00:01:00", "tenant": "alpha", "api": "supplier_down"}) + "\n"
    )

    with patch("should_retry.load_active_tenants", return_value=["alpha"]):
        with patch("should_retry.STATUS_DIR", str(tmp_path)):
            assert should_retry.main() == 0

def test_should_retry_not_needed_if_already_ok(tmp_path):
    ar_tz = timezone(timedelta(hours=-3))
    today_str = datetime.now(ar_tz).strftime("%Y-%m-%d")

    metrics_file = tmp_path / "metrics.jsonl"
    metrics_file.write_text(
        json.dumps({"ts": f"{today_str}T00:01:00", "tenant": "alpha", "api": "supplier_down"}) + "\n" +
        json.dumps({"ts": f"{today_str}T02:00:00", "tenant": "alpha", "api": "ok"}) + "\n"
    )

    with patch("should_retry.load_active_tenants", return_value=["alpha"]):
        with patch("should_retry.STATUS_DIR", str(tmp_path)):
            assert should_retry.main() == 1

def test_should_retry_no_runs_today(tmp_path):
    # Runs from yesterday, no runs today
    yesterday = datetime.now(timezone(timedelta(hours=-3))) - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")

    metrics_file = tmp_path / "metrics.jsonl"
    metrics_file.write_text(
        json.dumps({"ts": f"{yesterday_str}T00:01:00", "tenant": "alpha", "api": "supplier_down"}) + "\n"
    )

    with patch("should_retry.load_active_tenants", return_value=["alpha"]):
        with patch("should_retry.STATUS_DIR", str(tmp_path)):
            assert should_retry.main() == 1
