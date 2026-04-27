"""Tests de `_read_json` et de l'exception handler corrupted_cache (W7)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api
from modules import api_core


def test_read_json_returns_default_when_missing(tmp_path: Path):
    missing = tmp_path / "not_here.json"
    assert api._read_json(missing, default={"k": 1}) == {"k": 1}
    assert api._read_json(missing) == {}


def test_read_json_returns_default_when_empty(tmp_path: Path):
    empty = tmp_path / "empty.json"
    empty.write_text("")
    assert api._read_json(empty, default={"k": 1}) == {"k": 1}


def test_read_json_parses_valid(tmp_path: Path):
    good = tmp_path / "ok.json"
    good.write_text(json.dumps({"a": 1, "b": [1, 2]}))
    assert api._read_json(good) == {"a": 1, "b": [1, 2]}


def test_read_json_raises_corrupted_on_invalid(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{this is not valid json,,,")
    with pytest.raises(api.CorruptedCacheError) as excinfo:
        api._read_json(bad)
    assert excinfo.value.path == bad
    assert isinstance(excinfo.value.original, json.JSONDecodeError)


def test_read_json_raises_on_truncated(tmp_path: Path):
    """Simule un kill -9 pendant un write : JSON tronqué."""
    bad = tmp_path / "truncated.json"
    bad.write_text('{"a": 1, "b": [1, 2')
    with pytest.raises(api.CorruptedCacheError):
        api._read_json(bad)


def test_endpoint_returns_503_on_corrupted_equity(monkeypatch, tmp_path: Path):
    """End-to-end : un endpoint qui lit un cache corrompu remonte 503 typé."""
    bad = tmp_path / "equity_state.json"
    bad.write_text("{corrupted")
    monkeypatch.setattr(api_core, "EQUITY_PATH", bad)

    client = TestClient(api.app, raise_server_exceptions=False)
    resp = client.get("/api/status")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "corrupted_cache"
    assert body["cache_file"] == "equity_state.json"
    assert "recovery" in body
