/**
 * app/static/js/map.js
 * Interactive Leaflet.js map controller for Lafayette Wave Enthusiasts.
 * Uses 100% keyless Esri ArcGIS basemaps (Esri World Dark Gray Canvas,
 * Esri Ocean Shelf Bathymetry, and Esri Coastal Satellite Imagery) so there is
 * NEVER an "API KEY REQUIRED" watermark.
 * Features animated marching wave-crest markers whose speed, thickness,
 * and direction respond to live and 48h-scrubbed wave height & period predictions.
 */

const LA_COAST_CENTER = [29.58, -90.50];
const DEFAULT_ZOOM = 8.0;

const BADGE_COLOR_HEX = {
  purple: '#a855f7',
  emerald: '#10b981',
  amber: '#f59e0b',
  crimson: '#38bdf8',
};

function scoreToColorHex(score) {
  if (score >= 80) return '#a855f7';
  if (score >= 56) return '#10b981';
  if (score >= 31) return '#f59e0b';
  return '#38bdf8';
}

export class SurfMapController {
  constructor(mapElementId, onSpotSelect) {
    this.mapElementId = mapElementId;
    this.onSpotSelect = onSpotSelect;
    this.map = null;
    this.spotMarkers = new Map();
    this.buoyMarkers = new Map();
    this.shoalingLines = [];
    this.showBuoys = true;
    this.basemapPresets = [];
    this.activeBasemapIndex = 0;
  }

  init() {
    const container = document.getElementById(this.mapElementId);
    if (!container || typeof window.L === 'undefined') {
      return;
    }

    this.map = L.map(this.mapElementId, {
      center: LA_COAST_CENTER,
      zoom: DEFAULT_ZOOM,
      zoomSnap: 0.1,
      zoomDelta: 0.5,
      minZoom: 5,
      maxZoom: 16,
      zoomControl: false,
    });

    L.control.zoom({ position: 'topleft' }).addTo(this.map);

    // 1. GEBCO 3D Ocean Floor Topography & Shaded Relief (Primary Default Basemap)
    const oceanFloorBase = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}',
      {
        attribution: 'Tiles &copy; Esri &mdash; GEBCO, NOAA',
        maxZoom: 13,
      }
    );
    const oceanFloorRef = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Reference/MapServer/tile/{z}/{y}/{x}',
      {
        maxZoom: 13,
      }
    );
    const gebcoRelief = L.tileLayer.wms(
      'https://wms.gebco.net/mapserv?',
      {
        layers: 'GEBCO_LATEST',
        format: 'image/png',
        transparent: true,
        version: '1.3.0',
        opacity: 0.70,
        attribution: 'GEBCO 3D Relief &copy; IHO/IOC UNESCO',
        maxZoom: 14,
      }
    );
    const gebco3DGroup = L.layerGroup([oceanFloorBase, gebcoRelief, oceanFloorRef]);

    // 2. Coastal Satellite Hybrid (High-Res Aerial + Place Labels)
    const esriSatBase = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      {
        attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics',
        maxZoom: 17,
      }
    );
    const esriSatRef = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
      {
        maxZoom: 17,
      }
    );
    const esriSatGroup = L.layerGroup([esriSatBase, esriSatRef]);

    this.basemapPresets = [
      { key: '3d_relief', label: '3D Relief', fullName: 'GEBCO 3D Ocean Relief', group: gebco3DGroup, themeClass: 'map-theme-3d-relief' },
      { key: 'satellite', label: 'Satellite', fullName: 'Coastal Satellite Hybrid', group: esriSatGroup, themeClass: 'map-theme-satellite' },
    ];
    this.activeBasemapIndex = 0;

    // Default to GEBCO 3D Ocean Relief
    gebco3DGroup.addTo(this.map);
    this._applyBasemapThemeClass('map-theme-3d-relief');
  }

  _applyBasemapThemeClass(themeClass) {
    const container = document.getElementById(this.mapElementId);
    if (!container) return;
    container.classList.remove(
      'map-theme-3d-relief',
      'map-theme-satellite',
      'map-theme-deep-ocean',
      'map-theme-daylight-ocean',
      'map-theme-natgeo',
      'map-theme-indigo-radar'
    );
    if (themeClass) {
      container.classList.add(themeClass);
    }
  }

  toggleBasemap() {
    if (!this.map || !this.basemapPresets?.length) return '3D Relief';
    const current = this.basemapPresets[this.activeBasemapIndex];
    if (current && this.map.hasLayer(current.group)) {
      this.map.removeLayer(current.group);
    }
    this.activeBasemapIndex = (this.activeBasemapIndex + 1) % this.basemapPresets.length;
    const next = this.basemapPresets[this.activeBasemapIndex];
    next.group.addTo(this.map);
    this._applyBasemapThemeClass(next.themeClass);
    return next.label;
  }

  cycleBasemap() {
    return this.toggleBasemap();
  }

  /**
   * Renders Holly Beach and Grand Isle with animated marching wave crests
   * driven by wave height (ft), period (s), and swell direction (deg).
   */
  renderSpots(spots, activeSpotId, scrubbedHourIndex = null) {
    if (!this.map || !Array.isArray(spots)) return;

    this.spotMarkers.forEach((m) => this.map.removeLayer(m));
    this.spotMarkers.clear();

    spots.forEach((spot) => {
      const lat = spot.coordinates?.latitude ?? spot.consensus?.latitude;
      const lon = spot.coordinates?.longitude ?? spot.consensus?.longitude;
      if (typeof lat !== 'number' || typeof lon !== 'number') return;

      const cons = spot.consensus || {};
      const timeline = cons.timeline_48h || [];
      const hourPt =
        scrubbedHourIndex !== null && scrubbedHourIndex > 0 && timeline[scrubbedHourIndex]
          ? timeline[scrubbedHourIndex]
          : null;

      const score = hourPt ? hourPt.surf_score : (cons.surf_score ?? 0);
      const heightFt = hourPt ? hourPt.breaking_height_ft : (cons.breaking_height_ft ?? 1.5);
      const periodSec = hourPt ? hourPt.dominant_period_sec : (cons.primary_swell_period_sec ?? 7.0);
      const swellFromDeg = hourPt ? hourPt.swell_direction_deg : (cons.primary_swell_direction_deg ?? 165);
      const swellCard = hourPt ? hourPt.swell_direction_cardinal : (cons.primary_swell_compass ?? 'SSE');

      const colorHex = hourPt ? scoreToColorHex(score) : (BADGE_COLOR_HEX[cons.badge_color] || scoreToColorHex(score));
      const isSelected = spot.spot_id === activeSpotId;

      // Propagation angle for marching wave crests (traveling from Gulf toward shore)
      const swellToDeg = (Number(swellFromDeg) + 180) % 360;
      // Animation speed & thickness driven by predicted wave physics
      const crestDurationSec = Math.max(1.8, Math.min(4.8, 18.0 / Math.max(4.0, periodSec))).toFixed(2);
      const crestThicknessPx = Math.max(2, Math.min(6, 1.5 + heightFt * 0.85)).toFixed(1);

      const pinHtml = `
        <div class="spot-wave-beacon ${isSelected ? 'selected' : ''}"
             style="--crest-duration: ${crestDurationSec}s; --crest-thickness: ${crestThicknessPx}px; --crest-color: #facc15;">
          <div class="spot-pin-pill" style="border-color: ${colorHex}; ${isSelected ? `box-shadow: 0 0 26px ${colorHex}88;` : ''}">
            <div class="spot-pin-score" style="color: ${colorHex}; background: ${colorHex}20;" title="Surf Score: ${score} out of 100">
              <span class="pin-score-lbl">SCORE</span>
              <strong class="pin-score-val">${score}</strong>
              <span class="pin-score-bar"><span style="width: ${Math.max(6, score)}%; background: ${colorHex};"></span></span>
            </div>
            <div class="spot-pin-meta">
              <span class="spot-pin-title">${spot.spot_id === 'holly_beach' ? 'Holly Beach' : 'Grand Isle'}</span>
              <div class="spot-pin-primary-row">
                <span class="spot-pin-wave-ht">${Number(heightFt).toFixed(1)} ft</span>
                <span class="spot-pin-swell-info">${Number(periodSec).toFixed(1)}s ${swellCard}</span>
              </div>
            </div>
          </div>
          <div class="spot-pin-stem"></div>
          <div class="spot-anchor-core">
            <span class="spot-anchor-ring"></span>
            <span class="spot-anchor-dot" style="background: ${colorHex}; box-shadow: 0 0 12px ${colorHex};"></span>
          </div>
          <div class="map-swell-crests" style="transform: rotate(${swellToDeg}deg);">
            <span class="swell-crest-arc arc-1"></span>
            <span class="swell-crest-arc arc-2"></span>
            <span class="swell-crest-arc arc-3"></span>
            <span class="swell-crest-arc arc-4"></span>
          </div>
        </div>
      `;

      const icon = L.divIcon({
        className: 'surf-spot-marker-container',
        html: pinHtml,
        iconSize: [190, 150],
        iconAnchor: [95, 115],
      });

      const marker = L.marker([lat, lon], { icon, zIndexOffset: isSelected ? 600 : 400 }).addTo(this.map);
      marker.on('click', () => {
        if (this.onSpotSelect) {
          this.onSpotSelect(spot.spot_id);
        }
      });
      this.spotMarkers.set(spot.spot_id, marker);
    });
  }

  /**
   * Renders NOAA NDBC wave buoys & CO-OPS tide gauges + subtle shelf propagation lines.
   */
  renderBuoys(buoys, spots = []) {
    if (!this.map || !Array.isArray(buoys)) return;

    this.buoyMarkers.forEach((m) => this.map.removeLayer(m));
    this.buoyMarkers.clear();
    this.shoalingLines.forEach((l) => this.map.removeLayer(l));
    this.shoalingLines = [];

    if (!this.showBuoys) return;

    const spotCoords = {};
    spots.forEach((s) => {
      if (s.coordinates) {
        spotCoords[s.spot_id] = [s.coordinates.latitude, s.coordinates.longitude];
      }
    });

    buoys.forEach((buoy) => {
      if (typeof buoy.latitude !== 'number' || typeof buoy.longitude !== 'number') return;

      const isTide = buoy.station_type === 'coops_tide_station' || buoy.station_type === 'tide_station';
      const metricText = isTide
        ? `Tide ${buoy.tide_level_ft != null ? `${buoy.tide_level_ft.toFixed(1)}ft` : '--'}`
        : `${buoy.wave_height_ft != null ? `${buoy.wave_height_ft.toFixed(1)}ft` : '--'}`;

      const borderColor = isTide ? '#2dd4bf' : '#fbbf24';
      const pinHtml = `
        <div class="buoy-map-pin" style="border-color: ${borderColor};">
          <span class="buoy-pin-dot" style="background: ${borderColor};"></span>
          <strong>${buoy.station_id}</strong>
          <span>${metricText}</span>
        </div>
      `;

      const icon = L.divIcon({
        className: 'buoy-marker-container',
        html: pinHtml,
        iconSize: [120, 26],
        // Offset inshore tide stations (CAPL1, GISL1) so they never overlap the spot wave crests
        iconAnchor: isTide ? [-22, -26] : [60, 13],
      });

      const isHollyBuoy = buoy.station_id === '42035' || buoy.station_id === 'CAPL1';
      const kNet = isHollyBuoy ? 0.65 : 0.82;
      const shoaledFt = buoy.wave_height_ft != null ? (buoy.wave_height_ft * kNet).toFixed(1) : null;

      const popupHtml = `
        <div class="buoy-popup-card">
          <div class="buoy-popup-header">
            <div class="buoy-popup-id">${isTide ? 'CO-OPS TIDE GAUGE' : 'NOAA WAVE BUOY'} &bull; ${buoy.station_id}</div>
            <div class="buoy-popup-title">${buoy.station_name}</div>
          </div>
          <div class="buoy-popup-grid">
            <div class="buoy-popup-stat">
              <span>RAW WAVE</span>
              <strong>${buoy.wave_height_ft != null ? `${buoy.wave_height_ft} ft` : 'Inshore'}</strong>
            </div>
            <div class="buoy-popup-stat">
              <span>SHOALED (K=${kNet})</span>
              <strong>${shoaledFt != null ? `~${shoaledFt} ft` : 'Tide Ref'}</strong>
            </div>
            <div class="buoy-popup-stat">
              <span>PERIOD</span>
              <strong>${buoy.dominant_period_sec != null ? `${buoy.dominant_period_sec}s` : '--'}</strong>
            </div>
            <div class="buoy-popup-stat">
              <span>WIND</span>
              <strong>${buoy.wind_speed_mph != null ? `${buoy.wind_speed_mph} mph ${buoy.wind_direction_cardinal || ''}` : '--'}</strong>
            </div>
          </div>
        </div>
      `;

      const marker = L.marker([buoy.latitude, buoy.longitude], { icon })
        .bindPopup(popupHtml)
        .addTo(this.map);
      this.buoyMarkers.set(buoy.station_id, marker);

      if (!isTide) {
        const targetSpotCoord = buoy.station_id === '42035' ? spotCoords.holly_beach : spotCoords.grand_isle;
        if (targetSpotCoord) {
          const line = L.polyline([[buoy.latitude, buoy.longitude], targetSpotCoord], {
            color: '#38bdf8',
            weight: 1.5,
            opacity: 0.32,
            dashArray: '4, 8',
          }).addTo(this.map);
          this.shoalingLines.push(line);
        }
      }
    });
  }

  flyToSpot(spot) {
    if (!this.map || !spot) return;
    const lat = spot.coordinates?.latitude ?? spot.consensus?.latitude;
    const lon = spot.coordinates?.longitude ?? spot.consensus?.longitude;
    if (typeof lat === 'number' && typeof lon === 'number') {
      this.map.flyTo([lat, lon], 10, { duration: 0.85 });
    }
  }

  resetCoastView() {
    if (!this.map) return;
    this.map.flyTo(LA_COAST_CENTER, DEFAULT_ZOOM, { duration: 0.85 });
  }

  toggleBuoys(buoys, spots) {
    this.showBuoys = !this.showBuoys;
    this.renderBuoys(buoys, spots);
    return this.showBuoys;
  }
}
