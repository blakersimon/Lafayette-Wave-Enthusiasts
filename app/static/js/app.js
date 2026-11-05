/**
 * app/static/js/app.js
 * Main frontend orchestrator for Lafayette Wave Enthusiasts.
 * Coordinates the interactive Leaflet map, dual live HTML5 WaveTankAnimators
 * (Holly Beach & Grand Isle), Intuitive 48-Hour Forecast Sparkbar & Player,
 * and Unified Surf Cards.
 */

import { SurfMapController } from './map.js?v=13';
import { ForecastChartController } from './chart.js?v=13';
import { SpotDrawerController } from './drawer.js?v=13';
import { WaveTankAnimator } from './wave_animator.js?v=13';

const COLOR_HEX = {
  purple: '#a855f7',
  emerald: '#10b981',
  amber: '#f59e0b',
  crimson: '#38bdf8',
};

function scoreToBarColor(score) {
  if (score >= 80) return '#a855f7';
  if (score >= 56) return '#10b981';
  if (score >= 31) return '#f59e0b';
  return '#38bdf8';
}

class LouisianaSurfApp {
  constructor() {
    this.spots = [];
    this.buoys = [];
    this.comparison = null;
    this.activeSpotId = 'holly_beach';
    this.scrubbedHourIndex = null;
    this.timelinePlayTimer = null;

    // Two independent live WaveTankAnimator instances (one for each beach card!)
    this.waveAnimatorHB = new WaveTankAnimator('wave-tank-canvas', 'Holly Beach');
    this.waveAnimatorGI = new WaveTankAnimator('wave-tank-grand-isle', 'Grand Isle');

    this.chartController = new ForecastChartController('forecast-chart');
    this.drawerController = new SpotDrawerController(
      this.chartController,
      this.waveAnimatorHB,
      (spotId) => {
        this.selectSpot(spotId, true);
      },
      this.waveAnimatorGI
    );
    this.drawerController.setApp(this);
    this.mapController = new SurfMapController('surf-map', (spotId) => {
      this.selectSpot(spotId, true);
    });
  }

  async init() {
    this.waveAnimatorHB.init();
    this.waveAnimatorGI.init();
    this.mapController.init();
    this.bindUIEvents();
    await this.loadAllTelemetry();
  }

  bindUIEvents() {
    // Spot switcher pills in top bar
    document.querySelectorAll('.spot-pill').forEach((btn) => {
      btn.addEventListener('click', () => {
        const spotId = btn.getAttribute('data-spot-id');
        if (spotId) {
          this.selectSpot(spotId, true);
        }
      });
    });

    // Unified Beach Cards in right panel
    document.querySelectorAll('.beach-unified-card').forEach((card) => {
      card.addEventListener('click', () => {
        const spotId = card.getAttribute('data-spot-id');
        if (spotId) {
          this.selectSpot(spotId, true);
        }
      });
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          const spotId = card.getAttribute('data-spot-id');
          if (spotId) {
            this.selectSpot(spotId, true);
          }
        }
      });
    });

    // Segmented Card Tabs (Surf Check | 48h Chart | Sources)
    document.querySelectorAll('.card-tab').forEach((tabBtn) => {
      tabBtn.addEventListener('click', () => {
        const targetTab = tabBtn.getAttribute('data-tab');
        document.querySelectorAll('.card-tab').forEach((b) => {
          const active = b.getAttribute('data-tab') === targetTab;
          b.classList.toggle('active', active);
          b.setAttribute('aria-selected', String(active));
        });
        document.querySelectorAll('.tab-pane').forEach((pane) => {
          pane.classList.toggle('active', pane.id === `tab-${targetTab}`);
        });
      });
    });

    // Pause / Play Both Wave Tank Animations button
    const btnPauseWaves = document.getElementById('btn-pause-waves');
    const waveAnimIcon = document.getElementById('wave-anim-icon');
    if (btnPauseWaves) {
      btnPauseWaves.addEventListener('click', () => {
        const paused = this.waveAnimatorHB.togglePause();
        this.waveAnimatorGI.togglePause(paused);
        if (waveAnimIcon) {
          waveAnimIcon.innerHTML = paused ? '&#9654;' : '&#10074;&#10074;';
        }
      });
    }

    // 48-Hour Timeline Auto-Play Loop Button
    const btnPlayTimeline = document.getElementById('btn-play-timeline');
    if (btnPlayTimeline) {
      btnPlayTimeline.addEventListener('click', () => {
        this.toggleTimelinePlayback();
      });
    }

    // 48-Hour Time Scrubber Slider
    const slider = document.getElementById('forecast-time-slider');
    if (slider) {
      slider.addEventListener('input', (e) => {
        this.stopTimelinePlayback();
        const idx = Number(e.target.value);
        this.scrubToHour(idx === 0 ? null : idx);
      });
    }

    // Reset Scrubber to "Now"
    const btnScrubNow = document.getElementById('btn-scrub-now');
    if (btnScrubNow) {
      btnScrubNow.addEventListener('click', () => {
        this.stopTimelinePlayback();
        if (slider) slider.value = '0';
        this.setActiveQuickChip('btn-scrub-now');
        this.scrubToHour(null);
      });
    }

    // Jump to Tomorrow 6:00 AM Dawn Patrol
    const btnScrubDawn = document.getElementById('btn-scrub-dawn');
    if (btnScrubDawn) {
      btnScrubDawn.addEventListener('click', () => {
        this.stopTimelinePlayback();
        const dawnIdx = this.findNextDawnPatrolIndex();
        if (slider) slider.value = String(dawnIdx);
        this.setActiveQuickChip('btn-scrub-dawn');
        this.scrubToHour(dawnIdx);
      });
    }

    // Jump to Peak Golden Window
    const jumpToGolden = () => {
      this.stopTimelinePlayback();
      const bestIdx = this.findBestWindowIndex();
      if (slider) slider.value = String(bestIdx);
      this.setActiveQuickChip('btn-open-best-spot');
      this.scrubToHour(bestIdx);
    };

    const btnBestSpot = document.getElementById('btn-open-best-spot');
    if (btnBestSpot) btnBestSpot.addEventListener('click', jumpToGolden);

    const btnPreviewGolden = document.getElementById('btn-preview-golden-wave');
    if (btnPreviewGolden) btnPreviewGolden.addEventListener('click', jumpToGolden);

    // LA Coast full-view button
    const btnCoast = document.getElementById('btn-view-coast');
    if (btnCoast) {
      btnCoast.addEventListener('click', () => {
        this.mapController.resetCoastView();
      });
    }

    // 1-Click Basemap Style Switcher
    const btnCycleMap = document.getElementById('btn-cycle-basemap');
    if (btnCycleMap) {
      btnCycleMap.addEventListener('click', () => {
        const nextLabel = this.mapController.cycleBasemap();
        btnCycleMap.textContent = nextLabel;
      });
    }

    // 1-Click Bathymetry & Seafloor Overlays Switcher (Both, Contours, 3D Relief, OFF)
    const btnBathy = document.getElementById('btn-toggle-bathy');
    if (btnBathy) {
      btnBathy.addEventListener('click', () => {
        this.mapController.cycleBathy();
      });
    }

    // Buoy toggle button
    const btnBuoys = document.getElementById('btn-toggle-vectors');
    if (btnBuoys) {
      btnBuoys.addEventListener('click', () => {
        const shown = this.mapController.toggleBuoys(this.buoys, this.spots);
        btnBuoys.textContent = `Buoys: ${shown ? 'ON' : 'OFF'}`;
      });
    }

    // Refresh button
    const btnRefresh = document.getElementById('btn-refresh');
    if (btnRefresh) {
      btnRefresh.addEventListener('click', async () => {
        await this.refreshTelemetry();
      });
    }

    // Mobile card minimize toggle
    const btnDrawerToggle = document.getElementById('btn-drawer-toggle');
    const cardPanel = document.getElementById('spot-drawer');
    if (btnDrawerToggle && cardPanel) {
      btnDrawerToggle.addEventListener('click', () => {
        const minimized = cardPanel.classList.toggle('card-minimized');
        btnDrawerToggle.setAttribute('aria-expanded', String(!minimized));
      });
    }
  }

  setActiveQuickChip(activeId) {
    ['btn-scrub-now', 'btn-scrub-dawn', 'btn-open-best-spot'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.classList.toggle('active', id === activeId);
    });
  }

  findBestWindowIndex() {
    // Find hour index with highest combined or best surf score across both spots
    const hbTimeline = this.spots.find((s) => s.spot_id === 'holly_beach')?.consensus?.timeline_48h || [];
    const giTimeline = this.spots.find((s) => s.spot_id === 'grand_isle')?.consensus?.timeline_48h || [];
    const len = Math.max(hbTimeline.length, giTimeline.length);
    let bestIdx = 0;
    let bestScore = -1;

    for (let i = 0; i < len; i++) {
      const s1 = hbTimeline[i]?.surf_score ?? 0;
      const s2 = giTimeline[i]?.surf_score ?? 0;
      const maxS = Math.max(s1, s2);
      if (maxS > bestScore) {
        bestScore = maxS;
        bestIdx = i;
      }
    }
    return bestIdx;
  }

  findNextDawnPatrolIndex() {
    const spot = this.spots[0];
    const timeline = spot?.consensus?.timeline_48h || [];
    for (let i = 2; i < timeline.length; i++) {
      const dt = new Date(timeline[i].timestamp);
      if (!Number.isNaN(dt.getTime()) && (dt.getHours() === 6 || dt.getHours() === 7)) {
        return i;
      }
    }
    return Math.min(12, timeline.length - 1);
  }

  toggleTimelinePlayback() {
    if (this.timelinePlayTimer) {
      this.stopTimelinePlayback();
      return;
    }

    const playIcon = document.getElementById('scrubber-play-icon');
    const playBtn = document.getElementById('btn-play-timeline');
    if (playIcon) playIcon.innerHTML = '&#10074;&#10074;';
    if (playBtn) playBtn.classList.add('playing');

    const slider = document.getElementById('forecast-time-slider');
    const maxHour = Number(slider?.max || 47);

    this.timelinePlayTimer = setInterval(() => {
      let nextIdx = (this.scrubbedHourIndex ?? 0) + 1;
      if (nextIdx > maxHour) {
        nextIdx = 0;
      }
      if (slider) slider.value = String(nextIdx);
      this.setActiveQuickChip(nextIdx === 0 ? 'btn-scrub-now' : '');
      this.scrubToHour(nextIdx === 0 ? null : nextIdx);
    }, 480);
  }

  stopTimelinePlayback() {
    if (this.timelinePlayTimer) {
      clearInterval(this.timelinePlayTimer);
      this.timelinePlayTimer = null;
    }
    const playIcon = document.getElementById('scrubber-play-icon');
    const playBtn = document.getElementById('btn-play-timeline');
    if (playIcon) playIcon.innerHTML = '&#9654;';
    if (playBtn) playBtn.classList.remove('playing');
  }

  renderScrubberSparkbar() {
    const sparkContainer = document.getElementById('scrubber-sparkbar');
    const ticksContainer = document.getElementById('scrubber-ticks');
    if (!sparkContainer || !this.spots.length) return;

    const hbSpot = this.spots.find((s) => s.spot_id === 'holly_beach') || this.spots[0];
    const giSpot = this.spots.find((s) => s.spot_id === 'grand_isle') || this.spots[0];
    const hbTimeline = hbSpot?.consensus?.timeline_48h || [];
    const giTimeline = giSpot?.consensus?.timeline_48h || [];
    const count = Math.min(48, Math.max(hbTimeline.length, giTimeline.length));
    if (!count) return;

    const bestIdx = this.findBestWindowIndex();

    // Update "Best Window" button label with actual peak time & height
    const bestBtn = document.getElementById('btn-open-best-spot');
    const bestPt = (giTimeline[bestIdx]?.surf_score ?? 0) >= (hbTimeline[bestIdx]?.surf_score ?? 0)
      ? giTimeline[bestIdx]
      : hbTimeline[bestIdx];
    if (bestBtn && bestPt) {
      const dt = new Date(bestPt.timestamp);
      const timeLabel = Number.isNaN(dt.getTime())
        ? `+${bestIdx}h`
        : dt.toLocaleString([], { weekday: 'short', hour: 'numeric' });
      bestBtn.innerHTML = `&#9728; Best: ${timeLabel} (${Number(bestPt.breaking_height_ft ?? 1.5).toFixed(1)} ft)`;
    }

    // Find min & max wave height and score across 48h to accentuate peaks & troughs
    let minVal = Infinity;
    let maxVal = -Infinity;
    const combinedVals = [];
    for (let i = 0; i < count; i++) {
      const h1 = Number(hbTimeline[i]?.breaking_height_ft ?? 0);
      const h2 = Number(giTimeline[i]?.breaking_height_ft ?? 0);
      const s1 = Number(hbTimeline[i]?.surf_score ?? 0);
      const s2 = Number(giTimeline[i]?.surf_score ?? 0);
      const val = Math.max(h1, h2) * 20 + Math.max(s1, s2) * 0.6;
      combinedVals.push(val);
      if (val < minVal) minVal = val;
      if (val > maxVal) maxVal = val;
    }
    const span = Math.max(4, maxVal - minVal);

    // Build 48 interactive sparkbar columns
    const barsHtml = [];
    for (let i = 0; i < count; i++) {
      const h1 = Number(hbTimeline[i]?.breaking_height_ft ?? 0);
      const h2 = Number(giTimeline[i]?.breaking_height_ft ?? 0);
      const s1 = Number(hbTimeline[i]?.surf_score ?? 0);
      const s2 = Number(giTimeline[i]?.surf_score ?? 0);
      const peakH = Math.max(h1, h2);
      const peakS = Math.max(s1, s2);
      const norm = (combinedVals[i] - minVal) / span;
      const heightPct = Math.round(22 + norm * 78);
      const color = scoreToBarColor(peakS);
      const isBest = i === bestIdx;
      const isSelected = (this.scrubbedHourIndex ?? 0) === i;

      barsHtml.push(`
        <div class="spark-col ${isSelected ? 'active' : ''} ${isBest ? 'best-hour' : ''}"
             data-hour-idx="${i}"
             style="--bar-h: ${heightPct}%; --bar-color: ${color};"
             title="+${i}h: Holly ${h1.toFixed(1)}ft | Grand Isle ${h2.toFixed(1)}ft (Score ${peakS})">
          <span class="spark-bar"></span>
        </div>
      `);
    }
    sparkContainer.innerHTML = barsHtml.join('');

    // Allow clicking any sparkbar column to scrub directly to that hour
    sparkContainer.querySelectorAll('.spark-col').forEach((col) => {
      col.addEventListener('click', () => {
        this.stopTimelinePlayback();
        const idx = Number(col.getAttribute('data-hour-idx') || 0);
        const slider = document.getElementById('forecast-time-slider');
        if (slider) slider.value = String(idx);
        this.setActiveQuickChip(idx === 0 ? 'btn-scrub-now' : '');
        this.scrubToHour(idx === 0 ? null : idx);
      });
    });

    // Build real day & clock ticks along the bottom of the slider
    if (ticksContainer && hbTimeline.length > 0) {
      const tickIndices = [0, 9, 18, 27, 36, Math.min(47, count - 1)];
      ticksContainer.innerHTML = tickIndices
        .map((idx) => {
          if (idx === 0) return '<span class="scrubber-bound tick-now">Now</span>';
          const pt = hbTimeline[idx];
          if (!pt?.timestamp) return `<span class="scrubber-bound">+${idx}h</span>`;
          const dt = new Date(pt.timestamp);
          if (Number.isNaN(dt.getTime())) return `<span class="scrubber-bound">+${idx}h</span>`;
          const day = dt.toLocaleDateString('en-US', { weekday: 'short' });
          const hr = dt.toLocaleTimeString('en-US', { hour: 'numeric' }).replace(' ', '');
          return `<span class="scrubber-bound">${day} ${hr}</span>`;
        })
        .join('');
    }
  }

  updateSparkbarActiveColumn(hourIndex) {
    const activeIdx = hourIndex ?? 0;
    document.querySelectorAll('#scrubber-sparkbar .spark-col').forEach((col) => {
      const idx = Number(col.getAttribute('data-hour-idx'));
      col.classList.toggle('active', idx === activeIdx);
    });
  }

  scrubToHour(hourIndex) {
    this.scrubbedHourIndex = hourIndex;
    this.updateSparkbarActiveColumn(hourIndex);

    const hbSpot = this.spots.find((s) => s.spot_id === 'holly_beach') || this.spots[0];
    const giSpot = this.spots.find((s) => s.spot_id === 'grand_isle') || this.spots[0];
    const badgeEl = document.getElementById('scrubber-mode-badge');
    const timeEl = document.getElementById('scrubber-time-display');

    const hbCons = hbSpot?.consensus || {};
    const giCons = giSpot?.consensus || {};

    if (hourIndex === null || !hbCons.timeline_48h?.[hourIndex]) {
      if (badgeEl) {
        badgeEl.textContent = 'LIVE NOW';
        badgeEl.style.background = 'rgba(16, 185, 129, 0.22)';
        badgeEl.style.color = '#34d399';
      }
      const hbHt = Number(hbCons.breaking_height_ft ?? 0).toFixed(1);
      const giHt = Number(giCons.breaking_height_ft ?? 0).toFixed(1);
      if (timeEl) {
        timeEl.textContent = `Right Now • Holly ${hbHt} ft | Grand Isle ${giHt} ft`;
      }
      this.drawerController.renderSpot(hbSpot, this.comparison, null, this.spots, this.activeSpotId);
      this.mapController.renderSpots(this.spots, this.activeSpotId, null);
      this.renderTopBarPills(null);
      return;
    }

    const hbPt = hbCons.timeline_48h[hourIndex];
    const giPt = giCons.timeline_48h?.[hourIndex] || hbPt;
    const dt = new Date(hbPt.timestamp);
    const formatted = Number.isNaN(dt.getTime())
      ? `+${hourIndex}h Forecast`
      : dt.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });

    if (badgeEl) {
      badgeEl.textContent = `+${hourIndex}H FORECAST`;
      badgeEl.style.background = 'rgba(56, 189, 248, 0.24)';
      badgeEl.style.color = '#7dd3fc';
    }
    if (timeEl) {
      const hbHt = Number(hbPt.breaking_height_ft ?? 0).toFixed(1);
      const giHt = Number(giPt.breaking_height_ft ?? 0).toFixed(1);
      timeEl.textContent = `${formatted} • Holly ${hbHt} ft | Grand Isle ${giHt} ft`;
    }

    const activeSpot = this.spots.find((s) => s.spot_id === this.activeSpotId) || hbSpot;
    this.drawerController.renderSpot(activeSpot, this.comparison, hourIndex, this.spots, this.activeSpotId);
    this.mapController.renderSpots(this.spots, this.activeSpotId, hourIndex);
    this.renderTopBarPills(hourIndex);
  }

  async loadAllTelemetry() {
    this.updateStatusText('SYNCING GULF FEEDS...');
    try {
      const [spotsResp, buoysResp] = await Promise.all([
        fetch('/api/spots'),
        fetch('/api/buoys'),
      ]);

      const spotsPayload = await spotsResp.json();
      const buoysPayload = await buoysResp.json();

      this.spots = Array.isArray(spotsPayload) ? spotsPayload : spotsPayload.spots || [];
      this.comparison = spotsPayload.comparison || null;
      this.buoys = Array.isArray(buoysPayload) ? buoysPayload : [];

      this.renderScrubberSparkbar();
      this.renderTopBarPills(this.scrubbedHourIndex);
      this.mapController.renderSpots(this.spots, this.activeSpotId, this.scrubbedHourIndex);
      this.mapController.renderBuoys(this.buoys, this.spots);
      this.scrubToHour(this.scrubbedHourIndex);
      this.updateStatusText('GULF COAST SURF RADAR');
    } catch (err) {
      console.error('Failed to load telemetry:', err);
      this.updateStatusText('OFFLINE SNAPSHOT MODE');
    }
  }

  async refreshTelemetry() {
    this.updateStatusText('REFRESHING LIVE BUOYS & SITES...');
    try {
      const [spotsResp, buoysResp] = await Promise.all([
        fetch('/api/refresh', { method: 'POST' }),
        fetch('/api/buoys'),
      ]);
      const spotsPayload = await spotsResp.json();
      const buoysPayload = await buoysResp.json();

      this.spots = Array.isArray(spotsPayload) ? spotsPayload : spotsPayload.spots || [];
      this.comparison = spotsPayload.comparison || null;
      this.buoys = Array.isArray(buoysPayload) ? buoysPayload : [];

      this.renderScrubberSparkbar();
      this.renderTopBarPills(this.scrubbedHourIndex);
      this.mapController.renderSpots(this.spots, this.activeSpotId, this.scrubbedHourIndex);
      this.mapController.renderBuoys(this.buoys, this.spots);
      this.scrubToHour(this.scrubbedHourIndex);
      this.updateStatusText('LIVE TELEMETRY SYNCED');
    } catch (err) {
      console.error('Refresh failed:', err);
      this.updateStatusText('SYNC ERROR — USING CACHE');
    }
  }

  selectSpot(spotId, flyTo = false) {
    this.activeSpotId = spotId;

    document.querySelectorAll('.spot-pill').forEach((pill) => {
      pill.classList.toggle('active', pill.getAttribute('data-spot-id') === spotId);
    });

    const spot = this.spots.find((s) => s.spot_id === spotId) || this.spots[0];
    if (!spot) return;

    this.drawerController.renderSpot(spot, this.comparison, this.scrubbedHourIndex, this.spots, spotId);
    this.mapController.renderSpots(this.spots, spotId, this.scrubbedHourIndex);

    if (flyTo) {
      this.mapController.flyToSpot(spot);
    }
  }

  renderTopBarPills(hourIndex = null) {
    this.spots.forEach((spot) => {
      const cons = spot.consensus || {};
      const pt = hourIndex !== null && cons.timeline_48h?.[hourIndex] ? cons.timeline_48h[hourIndex] : null;
      const id = spot.spot_id;
      const dotEl = document.getElementById(`dot-${id}`);
      const waveEl = document.getElementById(`wave-${id}`);
      const scoreEl = document.getElementById(`score-${id}`);
      const score = pt ? pt.surf_score : cons.surf_score;
      const heightFt = pt ? pt.breaking_height_ft : cons.breaking_height_ft;
      const hex = pt ? scoreToBarColor(score ?? 0) : (COLOR_HEX[cons.badge_color] || '#10b981');

      if (dotEl) dotEl.style.background = hex;
      if (waveEl) waveEl.textContent = `${Number(heightFt ?? 0).toFixed(1)} ft`;
      if (scoreEl) {
        scoreEl.textContent = String(score ?? '--');
        scoreEl.title = `Surf Score: ${score ?? '--'} out of 100`;
        scoreEl.style.color = hex;
        scoreEl.style.background = `${hex}28`;
      }
    });
  }

  updateStatusText(label) {
    const text = document.getElementById('status-text');
    if (text) text.textContent = label;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = new LouisianaSurfApp();
  app.init();
});
