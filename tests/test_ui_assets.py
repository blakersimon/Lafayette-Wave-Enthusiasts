"""
tests/test_ui_assets.py
Comprehensive verification for Gen 2 UI enhancements:
1. Lafayette / Acadiana flag logo SVG heraldic structure and curling Gulf surf wave.
2. CSS styling for the enlarged emblem, decoupled map marker beacon, sticky table headers, and wind regimes.
3. JavaScript decoupled map marker architecture with yellow moving wave crests.
4. Chart.js point click scrubber interaction.
5. HTML template structure for relocated confidence pill and wetsuit notes.
6. FastAPI static asset serving of all updated files.
"""

from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_lafayette_logo_svg_heraldic_elements():
    logo_path = PROJECT_ROOT / "app" / "static" / "img" / "logo.svg"
    assert logo_path.exists(), "logo.svg must exist"
    content = logo_path.read_text(encoding="utf-8")

    # 1. ViewBox and SVG shell
    assert 'viewBox="0 0 100 100"' in content
    assert "<svg" in content and "</svg>" in content

    # 2. Quadrant 1: French Heritage (Royal Blue + White Fleur-de-lis)
    assert "acadianaBlue" in content
    assert "#2563eb" in content
    assert "#ffffff" in content  # white fleur-de-lis
    assert "Fleur-de-lis" in content or "petal" in content or "M0 -18" in content

    # 3. Quadrant 2: Spanish Heritage (Silver/White + Gold Castle/Tower of Castile)
    assert "acadianaWhite" in content
    assert "castileGold" in content or "#f59e0b" in content
    assert "Castle" in content or "turrets" in content or "M-14 13" in content

    # 4. Quadrant 3: Acadiana Heritage (Crimson Red + Gold 5-pointed Star)
    assert "acadianaRed" in content
    assert "starGold" in content or "#fcd34d" in content
    assert "polygon" in content  # 5-pointed star polygon
    assert "points=" in content

    # 5. Foreground Curling Gulf Surf Wave Barrel
    assert "gulfWaveBody" in content
    assert "waveHollowDark" in content
    assert "crestFoam" in content

    # 6. Outer perimeter ring
    assert "rimGrad" in content
    assert '<circle cx="50" cy="50" r="46"' in content


def test_brand_emblem_and_layout_in_css():
    css_path = PROJECT_ROOT / "app" / "static" / "css" / "styles.css"
    assert css_path.exists(), "styles.css must exist"
    css = css_path.read_text(encoding="utf-8")

    # 1. Brand emblem sizing & styling (64px badge)
    assert "inline-size: 64px;" in css
    assert "block-size: 64px;" in css
    assert "brand-wave-emblem" in css

    # 2. Decoupled marker architecture
    assert "inline-size: 190px;" in css
    assert "block-size: 150px;" in css
    assert ".spot-pin-pill {" in css
    assert "top: 6px;" in css
    assert ".spot-pin-stem {" in css
    assert ".spot-anchor-core {" in css
    assert ".spot-anchor-ring {" in css
    assert "beaconPing" in css

    # 3. Yellow wave crests styling
    assert "--crest-color, #facc15" in css or "#facc15" in css
    assert "marchWaveCrest" in css

    # 4. Sticky table headers
    assert ".source-comparison-table thead th {" in css
    assert "position: sticky;" in css
    assert "top: 0;" in css

    # 5. Color-coded wind regime badges
    assert ".regime-offshore" in css
    assert ".regime-cross" in css
    assert ".regime-onshore" in css


def test_map_js_decoupled_marker_architecture():
    js_path = PROJECT_ROOT / "app" / "static" / "js" / "map.js"
    assert js_path.exists(), "map.js must exist"
    code = js_path.read_text(encoding="utf-8")

    # Icon geometry
    assert "iconSize: [190, 150]" in code
    assert "iconAnchor: [95, 115]" in code

    # Yellow wave crest color
    assert "--crest-color: #facc15" in code

    # Decoupled marker elements
    assert "spot-pin-stem" in code
    assert "spot-anchor-core" in code
    assert "spot-anchor-ring" in code
    assert "spot-anchor-dot" in code
    assert "map-swell-crests" in code


def test_chart_js_scrubbing_interaction():
    js_path = PROJECT_ROOT / "app" / "static" / "js" / "chart.js"
    assert js_path.exists(), "chart.js must exist"
    code = js_path.read_text(encoding="utf-8")

    # Chart onClick handler to scrub slider
    assert "onClick:" in code
    assert "forecast-time-slider" in code
    assert "slider.value = String(idx)" in code


def test_drawer_js_presentation_updates():
    drawer_path = PROJECT_ROOT / "app" / "static" / "js" / "drawer.js"
    assert drawer_path.exists(), "drawer.js must exist"
    code = drawer_path.read_text(encoding="utf-8")

    # Wind regime styling
    assert "regime-offshore" in code
    assert "regime-onshore" in code
    assert "vital-wind-regime" in code

    # Wetsuit / water notes
    assert "vital-water-notes" in code
    assert "Boardshorts" in code


def test_html_template_hero_confidence_and_wetsuit_structure():
    html_path = PROJECT_ROOT / "app" / "templates" / "index.html"
    assert html_path.exists(), "index.html must exist"
    html = html_path.read_text(encoding="utf-8")

    # Emblem image sizing
    assert 'width="64" height="64"' in html
    assert 'alt="Lafayette Wave Enthusiasts Emblem"' in html

    # Confidence pill in hero overlay
    assert 'class="confidence-pill" id="hero-confidence-pill"' in html

    # Wetsuit notes under water temp
    assert 'id="vital-water-notes"' in html


def test_api_serves_updated_assets():
    with TestClient(app) as client:
        # 1. Logo
        resp_logo = client.get("/static/img/logo.svg")
        assert resp_logo.status_code == 200
        assert "acadianaBlue" in resp_logo.text
        assert "castileGold" in resp_logo.text
        assert "starGold" in resp_logo.text

        # 2. CSS
        resp_css = client.get("/static/css/styles.css")
        assert resp_css.status_code == 200
        assert "spot-anchor-core" in resp_css.text
        assert "regime-offshore" in resp_css.text

        # 3. Map JS
        resp_map = client.get("/static/js/map.js")
        assert resp_map.status_code == 200
        assert "--crest-color: #facc15" in resp_map.text

        # 4. Index HTML
        resp_index = client.get("/")
        assert resp_index.status_code == 200
        assert "vital-water-notes" in resp_index.text
        assert "hero-confidence-pill" in resp_index.text

        # 5. Gulf States JS module
        resp_gulf = client.get("/static/js/gulf_states.js")
        assert resp_gulf.status_code == 200
        assert "GULF_STATES_GEOJSON" in resp_gulf.text


def test_gulf_states_and_boundary_styling():
    js_path = PROJECT_ROOT / "app" / "static" / "js" / "map.js"
    assert js_path.exists()
    code = js_path.read_text(encoding="utf-8")

    # Natural shaded relief land mass with thick dark grey state boundary lines (no solid flat fill)
    assert "GULF_STATES_GEOJSON" in code
    assert "GULF_OCEAN_GEOJSON" in code
    assert "oceanDepthLayer" in code
    assert "fillColor: '#1e3347'" in code
    assert "fill: false" in code
    assert "stroke: true" in code
    assert "color: '#334155'" in code
    assert "weight: 2.2" in code
    assert "zoomSnap: 1" in code
