/**
 * app/static/js/drawer.js
 * Populates the Simplified Dual-Beach Surf Panel for Lafayette Wave Enthusiasts:
 * - Drives TWO simultaneous live HTML5 WaveTankAnimators (one for Holly Beach, one for Grand Isle)
 * - Renders a clear 0-100 SURF SCORE progress bar on each beach card
 * - Integrates Swell Pulse, Wind Texture, Tide Stage, and Water & Air Temp directly into each card
 * - Populates Tab 2 (48-Hour Breaking Wave & Period Chart) and Tab 3 (Multi-Source Comparison Matrix)
 */

const COLOR_MAP = {
  purple: '#a855f7',
  emerald: '#10b981',
  amber: '#f59e0b',
  crimson: '#ef4444',
};

function scoreToBadgeClass(score) {
  if (score >= 80) return 'badge-purple';
  if (score >= 56) return 'badge-emerald';
  if (score >= 31) return 'badge-amber';
  return 'badge-crimson';
}

function scoreToColorHex(score) {
  if (score >= 80) return '#a855f7';
  if (score >= 56) return '#10b981';
  if (score >= 31) return '#f59e0b';
  return '#38bdf8';
}

function scoreToBarGradient(score) {
  if (score >= 80) return 'linear-gradient(90deg, #38bdf8 0%, #10b981 45%, #a855f7 100%)';
  if (score >= 56) return 'linear-gradient(90deg, #38bdf8 0%, #10b981 100%)';
  if (score >= 31) return 'linear-gradient(90deg, #38bdf8 0%, #f59e0b 100%)';
  return 'linear-gradient(90deg, #0284c7 0%, #38bdf8 100%)';
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatSourceTypeBadge(sourceType, sourceId) {
  const st = (sourceType || '').toLowerCase();
  const sid = (sourceId || '').toLowerCase();
  if (st.includes('scraper') || st.includes('scraped') || sid.includes('surf')) {
    return '<span class="source-type-tag tag-scraper">SURF SITE</span>';
  }
  if (st.includes('buoy') || st.includes('tide') || sid.includes('ndbc') || sid.includes('coops')) {
    return '<span class="source-type-tag tag-buoy">NOAA BUOY</span>';
  }
  return '<span class="source-type-tag">MODEL</span>';
}

function getAirTempF(spot, hourPt = null) {
  if (hourPt && hourPt.air_temp_f != null && !isNaN(hourPt.air_temp_f)) {
    return Math.round(Number(hourPt.air_temp_f));
  }
  if (!spot) return null;
  const cons = spot.consensus || {};
  if (cons.air_temp_f != null && !isNaN(cons.air_temp_f)) {
    return Math.round(Number(cons.air_temp_f));
  }
  if (spot.air_temp_f != null && !isNaN(spot.air_temp_f)) {
    return Math.round(Number(spot.air_temp_f));
  }
  const sources = spot.source_breakdown || cons.source_breakdown || [];
  const temps = sources
    .map((s) => s.air_temp_f)
    .filter((t) => t != null && typeof t === 'number' && !isNaN(t));
  if (temps.length > 0) {
    const avg = temps.reduce((a, b) => a + b, 0) / temps.length;
    return Math.round(avg);
  }
  return null;
}

function getSuitNote(waterTemp, fallbackWetsuit) {
  if (waterTemp != null) {
    if (waterTemp < 58) return '4/3mm + Booties';
    if (waterTemp < 66) return '3/2mm Fullsuit';
    if (waterTemp < 72) return '2mm Springsuit / Top';
    if (waterTemp < 78) return 'Top / Boardies';
    return 'Boardshorts';
  }
  return fallbackWetsuit || 'Boardshorts';
}

function formatPeakTime(isoString) {
  if (!isoString) return 'Tomorrow Morning';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return String(isoString);
    const day = d.toLocaleDateString('en-US', { weekday: 'short' });
    const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
    return `${day} ${time}`;
  } catch {
    return String(isoString);
  }
}

export class SpotDrawerController {
  constructor(chartController, waveAnimatorHB, onSelectSpot = null, waveAnimatorGI = null) {
    this.chartController = chartController;
    this.waveAnimator = waveAnimatorHB;
    this.waveAnimatorHB = waveAnimatorHB;
    this.waveAnimatorGI = waveAnimatorGI;
    this.onSelectSpot = onSelectSpot;
    this.allSpots = [];
    this.comparison = null;
    this.activeSpotId = 'holly_beach';
    this.lastSpotId = null;
  }

  setApp(app) {
    this.app = app;
  }

  renderSpot(spot, comparison = null, scrubbedHourIndex = null, allSpots = null, activeSpotId = null) {
    if (!spot && (!allSpots || allSpots.length === 0) && (!this.allSpots || this.allSpots.length === 0)) return;

    if (allSpots && Array.isArray(allSpots) && allSpots.length > 0) {
      this.allSpots = allSpots;
    } else if (this.app?.spots && this.app.spots.length > 0) {
      this.allSpots = this.app.spots;
    } else if (spot && (!this.allSpots || this.allSpots.length === 0)) {
      this.allSpots = [spot];
    }

    if (comparison) {
      this.comparison = comparison;
    } else if (this.app?.comparison) {
      this.comparison = this.app.comparison;
    }

    const currentSpot = spot || this.allSpots.find((s) => s.spot_id === activeSpotId) || this.allSpots[0];
    if (!currentSpot) return;

    this.activeSpotId = activeSpotId || currentSpot.spot_id || 'holly_beach';

    const cons = currentSpot.consensus || {};
    const timeline = cons.timeline_48h || [];
    const hourPt =
      scrubbedHourIndex !== null && scrubbedHourIndex >= 0 && timeline[scrubbedHourIndex]
        ? timeline[scrubbedHourIndex]
        : null;

    // Divergence banner
    const divBanner = document.getElementById('divergence-banner');
    if (divBanner) {
      if (cons.divergence_warning && !hourPt) {
        divBanner.classList.remove('hidden');
        setText('divergence-text', cons.divergence_warning);
      } else {
        divBanner.classList.add('hidden');
      }
    }

    // Render BOTH Unified Beach Cards (Holly Beach & Grand Isle) with their own live wave animations
    const hbSpot = this.allSpots.find((s) => s.spot_id === 'holly_beach') || currentSpot;
    const giSpot = this.allSpots.find((s) => s.spot_id === 'grand_isle');

    if (hbSpot) {
      this.populateUnifiedBeachCard(
        'hb',
        hbSpot,
        this.activeSpotId === 'holly_beach',
        scrubbedHourIndex,
        this.waveAnimatorHB
      );
    }
    if (giSpot) {
      this.populateUnifiedBeachCard(
        'gi',
        giSpot,
        this.activeSpotId === 'grand_isle',
        scrubbedHourIndex,
        this.waveAnimatorGI
      );
    }

    const spotChanged = this.lastSpotId !== this.activeSpotId;
    this.lastSpotId = this.activeSpotId;

    if (spotChanged || scrubbedHourIndex === null) {
      if (this.chartController) {
        this.chartController.render(timeline);
        if (scrubbedHourIndex !== null) {
          this.chartController.setScrubbedHour(scrubbedHourIndex);
        }
      }
      const sources = currentSpot.source_breakdown || cons.source_breakdown || [];
      setText('tab-source-count', String(sources.length));
      this.renderSourceMatrix(sources);
    } else if (this.chartController) {
      this.chartController.setScrubbedHour(scrubbedHourIndex);
    }
  }

  populateUnifiedBeachCard(prefix, spot, isActive, scrubbedHourIndex, animator) {
    const cardId = prefix === 'hb' ? 'spot-card-holly-beach' : 'spot-card-grand-isle';
    const card = document.getElementById(cardId);
    if (!card) return;

    const cons = spot.consensus || {};
    const timeline = cons.timeline_48h || [];
    const hourPt =
      scrubbedHourIndex !== null && scrubbedHourIndex >= 0 && timeline[scrubbedHourIndex]
        ? timeline[scrubbedHourIndex]
        : null;

    const score = Math.max(0, Math.min(100, Number(hourPt ? hourPt.surf_score : (cons.surf_score ?? 0))));
    const heightFt = Number(hourPt ? hourPt.breaking_height_ft : (cons.breaking_height_ft ?? 0));
    const rangeStr = hourPt ? hourPt.breaking_range : (cons.breaking_range_str || '--');
    const decision = hourPt ? hourPt.decision : (cons.overall_decision || 'Fair');
    const periodSec = Number(hourPt ? hourPt.dominant_period_sec : (cons.primary_swell_period_sec ?? 7.0));
    const swellDeg = Number(hourPt ? hourPt.swell_direction_deg : (cons.primary_swell_direction_deg ?? 165));
    const swellCard = hourPt ? hourPt.swell_direction_cardinal : (cons.primary_swell_compass || 'SSE');
    const windSpd = Number(hourPt ? hourPt.wind_speed_mph : (cons.wind_speed_mph ?? 10));
    const windCard = hourPt ? hourPt.wind_direction_cardinal : (cons.wind_compass || 'N');
    const windRegime = hourPt ? hourPt.wind_regime : (cons.wind_character || 'Light / Glassy');
    const tideHt = hourPt ? hourPt.tide_height_ft : cons.tide_height_ft;
    const tideState = hourPt ? hourPt.tide_status : (cons.tide_state || 'Rising');
    const waterTemp = cons.water_temp_f;
    const airTemp = getAirTempF(spot, hourPt);

    // 1. Update this beach's live WaveTankAnimator
    if (animator) {
      animator.updateConditions({
        heightFt,
        periodSec,
        swellCompass: swellCard,
        windSpeedMph: windSpd,
        windRegime,
        surfScore: score,
        spotName: spot.spot_name || (prefix === 'hb' ? 'Holly Beach' : 'Grand Isle'),
        timeLabel: hourPt ? 'FORECAST' : 'LIVE NOW',
      });
    }

    // 2. Highlight active card border
    card.classList.toggle('active-spot', isActive);
    card.setAttribute('aria-selected', String(isActive));

    // 3. Wave Height, Range, Decision & Confidence on Wave Stage
    setText(`${prefix}-breaking-ft`, `${heightFt.toFixed(1)} ft`);
    setText(`${prefix}-breaking-range`, rangeStr);

    const decisionEl = document.getElementById(`${prefix}-decision-text`);
    if (decisionEl) {
      decisionEl.textContent = decision;
      decisionEl.className = `decision-pill ${scoreToBadgeClass(score)}`;
    }

    const confText = `Conf: ${cons.confidence_score_pct ?? 89}%`;
    if (prefix === 'hb') {
      setText('hero-confidence-pill', confText);
      setText('hero-breaking-ft', `${heightFt.toFixed(1)} ft`);
      setText('hero-breaking-range', rangeStr);
      setText('hero-decision-badge', decision);
      setText('hero-surf-score', String(score));
    } else {
      setText('gi-confidence-pill', confText);
    }

    // 4. Update 0-100 SURF SCORE Bar
    setText(`${prefix}-surf-score`, String(score));
    const barFill = document.getElementById(`${prefix}-score-bar-fill`);
    if (barFill) {
      barFill.style.width = `${Math.max(5, score)}%`;
      barFill.style.background = scoreToBarGradient(score);
      barFill.style.boxShadow = `0 0 12px ${scoreToColorHex(score)}88`;
    }
    const progressEl = document.getElementById(`${prefix}-score-progressbar`);
    if (progressEl) {
      progressEl.setAttribute('aria-valuenow', String(score));
    }

    // 5. Update 2x2 Info Grid (Swell, Wind, Tide, Water & Air Temp)
    setText(`${prefix}-swell-pulse`, `${periodSec.toFixed(1)}s ${swellCard}`);
    setText(`${prefix}-swell-deg`, `${Math.round(swellDeg)}° • Shoaled K=${spot.shoaling_k_net ?? (prefix === 'hb' ? 0.65 : 0.82)}`);

    setText(`${prefix}-wind-val`, `${Math.round(windSpd)} mph ${windCard}`);
    const lower = (windRegime || '').toLowerCase();
    let regimeClass = 'regime-cross';
    if (lower.includes('offshore')) regimeClass = 'regime-offshore';
    else if (lower.includes('onshore')) regimeClass = 'regime-onshore';

    const regimeId = prefix === 'hb' ? 'vital-wind-regime' : 'gi-wind-regime';
    const regimeEl = document.getElementById(regimeId);
    if (regimeEl) {
      regimeEl.textContent = windRegime;
      regimeEl.className = `regime-badge ${regimeClass}`;
    }

    const tideHtStr = tideHt != null ? `${Number(tideHt).toFixed(1)} ft` : '-- ft';
    setText(`${prefix}-tide-ht`, tideHtStr);
    setText(`${prefix}-tide-val`, tideState || 'Rising');

    const waterStr = waterTemp != null ? `${Math.round(waterTemp)}°F` : '--°F';
    const airStr = airTemp != null ? `${Math.round(airTemp)}°F` : '--°F';
    const suitNote = getSuitNote(waterTemp, spot.wetsuit_recommendation?.wetsuit);

    setText(`${prefix}-water-temp`, `Water: ${waterStr}`);
    setText(`${prefix}-air-temp`, `Air: ${airStr}`);
    if (prefix === 'hb') {
      setText('vital-water-main', `Water: ${waterStr}`);
      setText('vital-air-temp', airStr);
      setText('vital-water-notes', suitNote);
    } else {
      setText('gi-water-notes', suitNote);
    }

    // 6. Best 48h Window Footer & Board Pick
    const gw = spot.recommendation || cons.best_window || {};
    const peakTime = gw.peak_time
      ? formatPeakTime(gw.peak_time)
      : (gw.window_title ? gw.window_title.replace('Golden Window: ', '').replace('Marginal Window: ', '') : 'Tomorrow AM');
    const shortRange = (gw.peak_wave_range || '1-2 ft').split('(')[0].trim();
    setText(`gw-${prefix}-peak-time`, peakTime);
    setText(`gw-${prefix}-meta`, `• ${shortRange} • Score ${gw.peak_score ?? score}/100`);

    const boardStr = spot.board_recommendation?.primary || 'Longboard';
    let shortBoard = boardStr.split('(')[0].split(' or ')[0].trim();
    if (shortBoard.length > 18) shortBoard = shortBoard.replace('High-Volume ', '');
    setText(`${prefix}-board-pick`, shortBoard);
  }

  renderSourceMatrix(sources) {
    const tbody = document.getElementById('source-matrix-tbody');
    setText('matrix-source-summary', `${sources.length} feeds combined`);
    if (!tbody) return;

    if (!Array.isArray(sources) || sources.length === 0) {
      tbody.innerHTML = `
        <tr>
          <th scope="row">No Sources</th>
          <td colspan="6">Waiting for upstream telemetry...</td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = sources
      .map((src) => {
        const waveStr = src.wave_height_ft != null ? `${Number(src.wave_height_ft).toFixed(1)} ft` : 'Tide/Wind';
        const periodStr =
          src.wave_period_sec != null
            ? `${Number(src.wave_period_sec).toFixed(1)}s ${src.swell_direction_cardinal || ''}`
            : '--';
        const windStr =
          src.wind_speed_mph != null
            ? `${Math.round(src.wind_speed_mph)} mph ${src.wind_direction_cardinal || ''}`
            : '--';
        const condStr =
          src.raw_condition_label || (src.tide_ft != null ? `Tide ${src.tide_ft} ft (${src.tide_status || ''})` : 'Active');
        const weightPct = `${Math.round((src.confidence_weight ?? 0.15) * 100)}%`;
        const statusKey = src.status || 'valid';
        const statusLabel = statusKey === 'outlier_rejected' ? 'OUTLIER' : statusKey.toUpperCase();

        return `
          <tr>
            <th scope="row">
              <div>${escapeHtml(src.source_name || src.source_id)}</div>
              ${formatSourceTypeBadge(src.source_type, src.source_id)}
            </th>
            <td class="num-col"><strong>${escapeHtml(waveStr)}</strong></td>
            <td class="num-col">${escapeHtml(periodStr.trim())}</td>
            <td class="num-col">${escapeHtml(windStr.trim())}</td>
            <td>${escapeHtml(condStr)}</td>
            <td class="num-col">${escapeHtml(weightPct)}</td>
            <td><span class="source-status-pill status-${escapeHtml(statusKey)}">${escapeHtml(statusLabel)}</span></td>
          </tr>
        `;
      })
      .join('');
  }
}
