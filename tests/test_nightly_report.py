import os
import json
import pytest
from unittest.mock import patch, MagicMock

# Agregamos scripts al path para poder importar
import sys
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import nightly_report

@patch('nightly_report.requests.post')
@patch('nightly_report.GEMINI_API_KEY', 'fake_key')
def test_get_ai_analysis_success(mock_post):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": "Mocked AI Summary"}]}}
        ]
    }
    mock_post.return_value = mock_response

    accum_data = {"new": {}, "updated": {}}
    metrics_data = [{"api": "ok", "duration": 1.5, "updates": 2}]

    result = nightly_report.get_ai_analysis(accum_data, metrics_data)
    assert result == "Mocked AI Summary"
    mock_post.assert_called_once()

def test_analyze_infrastructure_empty():
    result = nightly_report.analyze_infrastructure([])
    assert "No hay métricas registradas hoy." in result

def test_analyze_infrastructure_stats():
    metrics = [
        {"api": "ok", "duration": 1.0, "updates": 1},
        {"api": "ok", "duration": 2.0, "updates": 2},
        {"api": "api_fail", "duration": 0.0, "updates": 0}
    ]
    result = nightly_report.analyze_infrastructure(metrics)
    assert "**Disponibilidad API:** 66.7%" in result
    assert "**Latencia Promedio:** 1.00s" in result
    assert "**Ejecuciones Hoy:** 3" in result
    assert "**Fallos:** 1" in result
