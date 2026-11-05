"""
tests/test_api_routes.py
Integration and End-to-End tests for the FastAPI application, REST API endpoints,
multi-source consensus payloads, static assets, and fallback resilience.
"""

from __future__ import annotations
import os
import pytest
from fastapi.testclient import TestClient

os.environ["DISABLE_BG_WARMUP"] = "1"

from app.main import app
from app.services.cache_service import get_cache_service


@pytest.fixture(autouse=True)
def reset_cache():
    cache = get_cache_service()
    cache.clear()
    cache.load_disk_fallbacks()
    yield


def test_index_html_shell():
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert "Lafayette Wave Enthusiasts" in html
        assert "/static/img/logo.svg" in html
        assert 'meta name="color-scheme" content="dark light"' in html
        assert 'id="surf-map"' in html
        assert 'id="wave-tank-canvas"' in html
        assert 'id="source-matrix-tbody"' in html
        assert "/static/js/app.js" in html


def test_static_assets_served():
    with TestClient(app) as client:
        css_resp = client.get("/static/css/styles.css")
        assert css_resp.status_code == 200
        assert "scrollbar-color" in css_resp.text
        assert "@container (width < 480px)" in css_resp.text
        assert "marchWaveCrest" in css_resp.text

        logo_resp = client.get("/static/img/logo.svg")
        assert logo_resp.status_code == 200
        assert "<svg" in logo_resp.text

        wave_js_resp = client.get("/static/js/wave_animator.js")
        assert wave_js_resp.status_code == 200
        assert "WaveTankAnimator" in wave_js_resp.text

        js_resp = client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        assert "LouisianaSurfApp" in js_resp.text


def test_health_endpoint():
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "louisiana-surf-finder"
        assert "holly_beach" in data["spots_loaded"]
        assert "grand_isle" in data["spots_loaded"]
        assert set(data["buoys_monitored"]) == {"42035", "42354", "42001", "CAPL1", "GISL1"}
        assert data["cache"]["is_warmed"] is True


def test_list_spots_and_multi_source_breakdown():
    with TestClient(app) as client:
        resp = client.get("/api/spots")
        assert resp.status_code == 200
        assert "X-Cache" in resp.headers
        payload = resp.json()
        assert "spots" in payload
        assert "comparison" in payload
        spots = payload["spots"]
        assert len(spots) == 2

        spot_ids = {s["spot_id"] for s in spots}
        assert spot_ids == {"holly_beach", "grand_isle"}

        for spot in spots:
            cons = spot["consensus"]
            assert 0 <= cons["surf_score"] <= 100
            assert cons["overall_decision"] in ("Poor / Flat", "Fair", "Good / Decent", "Firing / Epic")
            assert len(cons["timeline_48h"]) >= 24
            assert spot["board_recommendation"] is not None
            assert spot["wetsuit_recommendation"] is not None
            source_ids = {src["source_id"] for src in cons["source_breakdown"]}
            assert "surfline" in source_ids
            assert "surf_captain" in source_ids
            assert "open_meteo" in source_ids


def test_get_single_spot_and_404():
    with TestClient(app) as client:
        hb_resp = client.get("/api/spots/holly_beach")
        assert hb_resp.status_code == 200
        hb = hb_resp.json()
        assert hb["spot_id"] == "holly_beach"
        assert hb["shoaling_k_net"] == 0.65
        hb_sources = {s["source_id"] for s in hb["consensus"]["source_breakdown"]}
        assert {"surfline", "surf_forecast", "surf_captain"}.issubset(hb_sources)

        gi_resp = client.get("/api/spots/grand_isle")
        assert gi_resp.status_code == 200
        gi = gi_resp.json()
        assert gi["spot_id"] == "grand_isle"
        assert gi["shoaling_k_net"] == 0.82

        not_found = client.get("/api/spots/malibu")
        assert not_found.status_code == 404


def test_spot_forecast_and_sources_endpoints():
    with TestClient(app) as client:
        fc_resp = client.get("/api/spots/holly_beach/forecast?hours=24")
        assert fc_resp.status_code == 200
        fc = fc_resp.json()
        assert fc["spot_id"] == "holly_beach"
        assert len(fc["hourly"]) == 24

        src_resp = client.get("/api/spots/holly_beach/sources")
        assert src_resp.status_code == 200
        src_data = src_resp.json()
        assert src_data["spot_id"] == "holly_beach"
        assert len(src_data["sources"]) >= 5


def test_buoys_and_comparison_endpoints():
    with TestClient(app) as client:
        buoys_resp = client.get("/api/buoys")
        assert buoys_resp.status_code == 200
        buoys = buoys_resp.json()
        assert len(buoys) == 5
        ids = {b["station_id"] for b in buoys}
        assert ids == {"42035", "42354", "42001", "CAPL1", "GISL1"}

        b_single = client.get("/api/buoys/42035")
        assert b_single.status_code == 200
        assert b_single.json()["station_id"] == "42035"

        b_404 = client.get("/api/buoys/99999")
        assert b_404.status_code == 404

        comp_resp = client.get("/api/comparison")
        assert comp_resp.status_code == 200
        comp = comp_resp.json()
        assert "winner_spot" in comp
        assert "comparison_verdict" in comp


def test_porkbun_dns_automation_mocked():
    from unittest.mock import MagicMock, patch
    from scripts.setup_porkbun_dns import configure_porkbun_dns

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client

    def fake_post(url, json=None):
        resp = MagicMock()
        if "/dns/retrieve/" in url:
            resp.json.return_value = {
                "status": "SUCCESS",
                "records": [
                    {"id": "101", "name": "louisianasurffinder.com", "type": "A", "content": "1.2.3.4"},
                    {"id": "102", "name": "www.louisianasurffinder.com", "type": "CNAME", "content": "old.app"},
                ],
            }
        else:
            resp.json.return_value = {"status": "SUCCESS", "id": "201"}
        return resp

    mock_client.post.side_effect = fake_post

    with patch("scripts.setup_porkbun_dns.httpx.Client", return_value=mock_client):
        res = configure_porkbun_dns(
            domain="louisianasurffinder.com",
            railway_target="la-surf-prod.up.railway.app",
            api_key="pk1_test",
            secret_key="sk1_test",
            txt_name="_railway-verify",
            txt_value="verify-token-123",
        )
        assert res["domain"] == "louisianasurffinder.com"
        assert res["target"] == "la-surf-prod.up.railway.app"
        assert len(res["actions"]) == 5

