/**
 * app/static/js/wave_animator.js
 * Upgraded HTML5 Canvas Coastal Wave & Shoaling Simulator for Lafayette Wave Enthusiasts:
 * - Supports multiple simultaneous instances (one live wave tank per beach card: Holly Beach & Grand Isle)
 * - Simulates real left-to-right shoaling over a sloping coastal sandbar:
 *     * Deep Gulf groundswell enters on the left, steepens over the sandbar, and pitches/breaks on the right
 * - Translucent backlit emerald-cyan wave faces with subsurface light refraction
 * - Dynamic curling lip, tumbling whitewater foam rollers, marbled foam trails, and wind-driven spindrift spray
 * - Animated surfer carving the peeling wave face (when rideable) + gliding Gulf pelican
 * - Respects prefers-reduced-motion and supports synchronized Pause/Play.
 */

export class WaveTankAnimator {
  constructor(canvasId, defaultSpotName = 'Holly Beach') {
    this.canvasId = canvasId;
    this.canvas = null;
    this.ctx = null;
    this.rafId = null;
    this.isPaused = false;
    this.reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.phase = Math.random() * Math.PI * 2;

    this.targetState = {
      heightFt: 1.8,
      periodSec: 6.2,
      swellCompass: 'SSE',
      windSpeedMph: 8,
      windRegime: 'Cross-Shore',
      surfScore: 36,
      spotName: defaultSpotName,
      timeLabel: 'LIVE NOW',
    };

    this.currentState = { ...this.targetState };
    this.sprayParticles = [];
    this.foamBubbles = [];
  }

  init() {
    this.canvas = document.getElementById(this.canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');

    this._initParticles();
    this._handleResize();
    window.addEventListener('resize', () => this._handleResize(), { passive: true });

    if (this.reducedMotion) {
      this.isPaused = true;
      this.drawFrame(this.phase);
    } else {
      this.start();
    }
  }

  _initParticles() {
    this.sprayParticles = Array.from({ length: 32 }, () => ({
      seed: Math.random(),
      offsetX: (Math.random() - 0.5) * 24,
      offsetY: Math.random() * 12,
      size: 1.0 + Math.random() * 2.1,
      speed: 0.7 + Math.random() * 0.8,
    }));

    this.foamBubbles = Array.from({ length: 24 }, (_, i) => ({
      relX: (i / 24) + Math.random() * 0.04,
      relY: Math.random(),
      radius: 1.1 + Math.random() * 2.4,
      phaseOffset: Math.random() * Math.PI * 2,
    }));
  }

  _handleResize() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(260, Math.floor(rect.width || 380));
    const h = Math.max(110, Math.floor(rect.height || 138));
    this.canvas.width = w * dpr;
    this.canvas.height = h * dpr;
    this.width = w;
    this.height = h;
    if (this.ctx) {
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    if (this.isPaused) {
      this.drawFrame(this.phase);
    }
  }

  updateConditions({
    heightFt = 1.5,
    periodSec = 6.0,
    swellCompass = 'SSE',
    windSpeedMph = 8,
    windRegime = 'Cross-Shore',
    surfScore = 35,
    spotName = 'Holly Beach',
    timeLabel = 'LIVE NOW',
  }) {
    this.targetState = {
      heightFt: Math.max(0.3, Math.min(10.0, Number(heightFt) || 0.5)),
      periodSec: Math.max(3.5, Math.min(16.0, Number(periodSec) || 6.0)),
      swellCompass: swellCompass || 'SSE',
      windSpeedMph: Math.max(0, Number(windSpeedMph) || 8),
      windRegime: windRegime || 'Light / Glassy',
      surfScore: Number(surfScore) || 35,
      spotName,
      timeLabel,
    };
    if (this.isPaused) {
      this.currentState = { ...this.targetState };
      this.drawFrame(this.phase);
    }
  }

  togglePause(forceState = undefined) {
    this.isPaused = typeof forceState === 'boolean' ? forceState : !this.isPaused;
    if (!this.isPaused) {
      this.start();
    } else if (this.rafId) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    return this.isPaused;
  }

  start() {
    if (this.rafId) return;
    let lastTime = performance.now();

    const tick = (now) => {
      if (this.isPaused) {
        this.rafId = null;
        return;
      }
      const dt = Math.min(0.05, (now - lastTime) / 1000);
      lastTime = now;

      const lerp = 0.08;
      this.currentState.heightFt += (this.targetState.heightFt - this.currentState.heightFt) * lerp;
      this.currentState.periodSec += (this.targetState.periodSec - this.targetState.periodSec) * lerp;
      this.currentState.windSpeedMph += (this.targetState.windSpeedMph - this.currentState.windSpeedMph) * lerp;
      this.currentState.surfScore += (this.targetState.surfScore - this.currentState.surfScore) * lerp;
      this.currentState.windRegime = this.targetState.windRegime;
      this.currentState.swellCompass = this.targetState.swellCompass;
      this.currentState.spotName = this.targetState.spotName;
      this.currentState.timeLabel = this.targetState.timeLabel;

      const speedFactor = 9.0 / Math.max(4.2, this.currentState.periodSec);
      this.phase += dt * 2.35 * speedFactor;

      this.drawFrame(this.phase);
      this.rafId = requestAnimationFrame(tick);
    };

    this.rafId = requestAnimationFrame(tick);
  }

  drawFrame(phase) {
    const ctx = this.ctx;
    if (!ctx || !this.width || !this.height) return;

    const w = this.width;
    const h = this.height;
    const state = this.currentState;

    const isOffshore = state.windRegime.toLowerCase().includes('offshore');
    const isGlassy = state.windRegime.toLowerCase().includes('glassy');
    const isOnshore = state.windRegime.toLowerCase().includes('onshore');

    // 1. Rich Atmospheric Coastal Sky & Horizon Glow
    const skyGrad = ctx.createLinearGradient(0, 0, 0, h * 0.72);
    if (state.surfScore >= 56) {
      skyGrad.addColorStop(0, '#07192f');
      skyGrad.addColorStop(0.55, '#0c3b5e');
      skyGrad.addColorStop(1, '#155e75');
    } else if (state.surfScore >= 31) {
      skyGrad.addColorStop(0, '#08172b');
      skyGrad.addColorStop(0.58, '#0f3152');
      skyGrad.addColorStop(1, '#164e63');
    } else {
      skyGrad.addColorStop(0, '#091526');
      skyGrad.addColorStop(0.6, '#12263f');
      skyGrad.addColorStop(1, '#1e3a5f');
    }
    ctx.fillStyle = skyGrad;
    ctx.fillRect(0, 0, w, h);

    // Subtle warm sun/horizon bloom on the right side
    const sunGrad = ctx.createRadialGradient(w * 0.78, h * 0.28, 4, w * 0.78, h * 0.28, w * 0.45);
    sunGrad.addColorStop(0, state.surfScore >= 31 ? 'rgba(253, 224, 71, 0.22)' : 'rgba(56, 189, 248, 0.16)');
    sunGrad.addColorStop(0.5, 'rgba(56, 189, 248, 0.06)');
    sunGrad.addColorStop(1, 'rgba(15, 23, 42, 0)');
    ctx.fillStyle = sunGrad;
    ctx.fillRect(0, 0, w, h);

    // 2. Gliding Gulf Pelican in Sky
    this._drawPelican(ctx, w, h, phase);

    // 3. Reference Wave Scale Lines on Left (2ft, 4ft, 6ft)
    const baseWaterY = h * 0.67;
    const pxPerFoot = Math.min(8.8, (h * 0.42) / 5.5);

    ctx.save();
    ctx.strokeStyle = 'rgba(148, 163, 184, 0.13)';
    ctx.fillStyle = 'rgba(186, 230, 253, 0.42)';
    ctx.font = "600 9px 'JetBrains Mono', monospace";
    ctx.setLineDash([2, 5]);
    [2, 4, 6].forEach((ftMark) => {
      const yMark = baseWaterY - ftMark * pxPerFoot;
      if (yMark > 14) {
        ctx.beginPath();
        ctx.moveTo(0, yMark);
        ctx.lineTo(w, yMark);
        ctx.stroke();
        ctx.fillText(`${ftMark}ft`, 6, yMark - 2);
      }
    });
    ctx.restore();

    // Compute physical wave geometry
    // Boost visual wave presence slightly for small waves so 0.8ft vs 1.8ft difference is super clear
    const visualFt = Math.pow(state.heightFt, 0.88) * 1.18;
    const ampPx = Math.max(4.5, Math.min(h * 0.36, visualFt * pxPerFoot));
    const wavelengthPx = Math.max(88, Math.min(270, state.periodSec * 21));
    const k = (2 * Math.PI) / wavelengthPx;
    const chopFactor = isGlassy ? 0.02 : isOffshore ? 0.06 : isOnshore ? 0.34 : 0.15;

    // 4. Deep Horizon Swell Layer
    this._drawShoalingWaveLayer(ctx, {
      w,
      h,
      baseY: baseWaterY - 7,
      amp: ampPx * 0.55,
      k: k * 0.82,
      phase: phase * 0.68 + 2.1,
      chopFactor: chopFactor * 0.45,
      steepness: 0.22,
      shoalGain: 0.15,
      fillTop: 'rgba(14, 116, 144, 0.42)',
      fillMid: 'rgba(12, 74, 110, 0.65)',
      fillBottom: 'rgba(7, 24, 46, 0.9)',
      strokeColor: 'rgba(125, 211, 252, 0.25)',
      returnCrests: false,
    });

    // 5. Midground Groundswell Train
    this._drawShoalingWaveLayer(ctx, {
      w,
      h,
      baseY: baseWaterY - 1,
      amp: ampPx * 0.78,
      k: k * 0.92,
      phase: phase * 0.86 + 0.9,
      chopFactor: chopFactor * 0.7,
      steepness: 0.3,
      shoalGain: 0.28,
      fillTop: 'rgba(6, 182, 212, 0.52)',
      fillMid: 'rgba(3, 105, 161, 0.75)',
      fillBottom: 'rgba(6, 20, 39, 0.94)',
      strokeColor: 'rgba(103, 232, 249, 0.42)',
      returnCrests: false,
    });

    // 6. Primary Shoaling & Breaking Sandbar Wave Layer
    const crestPoints = this._drawShoalingWaveLayer(ctx, {
      w,
      h,
      baseY: baseWaterY + 6,
      amp: ampPx,
      k,
      phase,
      chopFactor,
      steepness: isOffshore ? 0.48 : 0.36,
      shoalGain: 0.42,
      fillTop: state.surfScore >= 52 ? 'rgba(45, 212, 191, 0.86)' : 'rgba(34, 211, 238, 0.78)',
      fillMid: 'rgba(2, 132, 199, 0.88)',
      fillBottom: 'rgba(4, 15, 31, 0.98)',
      strokeColor: '#a5f3fc',
      returnCrests: true,
    });

    // 7. Curling Lip, Tumbling Whitewater, Spindrift Spray & Surfer/Boarder
    this._drawBreakingFoamAndSurfer(ctx, crestPoints, state, ampPx, w, h, baseWaterY, isOffshore, isOnshore);

    // 8. Sloping Coastal Sandbar / Shoreline Swash in Foreground
    this._drawShorelineSandbarAndSwash(ctx, w, h, ampPx, k, phase, chopFactor);
  }

  _drawPelican(ctx, w, h, phase) {
    const glideX = ((phase * 14) % (w + 80)) - 40;
    const glideY = 22 + Math.sin(phase * 1.4) * 3.5;
    const wingFlap = Math.sin(phase * 3.2) * 2.8;

    ctx.save();
    ctx.strokeStyle = 'rgba(226, 232, 240, 0.48)';
    ctx.lineWidth = 1.4;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(glideX - 7, glideY - wingFlap);
    ctx.quadraticCurveTo(glideX - 3, glideY - 3, glideX, glideY);
    ctx.quadraticCurveTo(glideX + 3, glideY - 3, glideX + 7, glideY - wingFlap);
    ctx.stroke();
    ctx.restore();
  }

  _drawShoalingWaveLayer(
    ctx,
    { w, h, baseY, amp, k, phase, chopFactor, steepness, shoalGain, fillTop, fillMid, fillBottom, strokeColor, returnCrests }
  ) {
    const crests = [];
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(0, h);

    let prevDy = 0;
    let prevY = baseY;

    for (let x = 0; x <= w; x += 3) {
      // Shoaling factor increases as wave approaches right-hand sandbar (x/w from 0 to 1)
      const normX = x / w;
      const shoalMultiplier = 0.82 + shoalGain * Math.sin(normX * Math.PI * 0.85);
      const localAmp = amp * shoalMultiplier;

      // Skew wave forward as it shoals
      const theta = k * x - phase + normX * 0.35;
      const primary =
        Math.sin(theta) +
        steepness * Math.sin(2 * theta + 0.45) +
        steepness * 0.32 * Math.sin(3 * theta + 0.9);
      const chop =
        chopFactor *
        (0.6 * Math.sin(4.1 * theta + phase * 1.7) + 0.4 * Math.cos(6.7 * theta - phase * 2.2));

      const y = baseY - localAmp * (primary * 0.74 + chop);
      ctx.lineTo(x, y);

      const dy = prevY - y; // rising toward crest when dy > 0
      if (returnCrests && prevDy > 0 && dy <= 0 && x > 36 && x < w - 32) {
        crests.push({ x, y, localAmp, normX });
      }
      prevDy = dy;
      prevY = y;
    }

    ctx.lineTo(w, h);
    ctx.closePath();

    const grad = ctx.createLinearGradient(0, baseY - amp * 1.25, 0, h);
    grad.addColorStop(0, fillTop);
    grad.addColorStop(0.42, fillMid);
    grad.addColorStop(1, fillBottom);
    ctx.fillStyle = grad;
    ctx.fill();

    // Sunlit subsurface caustic lines under the wave face
    if (returnCrests && crests.length > 0) {
      ctx.strokeStyle = 'rgba(165, 243, 252, 0.18)';
      ctx.lineWidth = 1.2;
      crests.forEach((c) => {
        ctx.beginPath();
        ctx.moveTo(c.x - 16, c.y + 4);
        ctx.quadraticCurveTo(c.x + 6, c.y + c.localAmp * 0.45, c.x + 24, c.y + c.localAmp * 0.85);
        ctx.stroke();
      });
    }

    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = returnCrests ? 2.2 : 1.4;
    ctx.stroke();
    ctx.restore();

    return crests;
  }

  _drawBreakingFoamAndSurfer(ctx, crests, state, ampPx, w, h, baseWaterY, isOffshore, isOnshore) {
    if (!crests.length) return;
    ctx.save();

    crests.forEach((crest, idx) => {
      const foamWidth = Math.min(42, 9 + state.heightFt * 5.8) * (0.85 + crest.normX * 0.35);
      const lipHeight = Math.max(2.5, crest.localAmp * 0.22);

      // 1. Pitching Curling Wave Lip & Hollow Pocket Shadow (when height >= 1.1 ft)
      if (state.heightFt >= 1.1) {
        ctx.fillStyle = 'rgba(3, 105, 161, 0.72)';
        ctx.beginPath();
        ctx.ellipse(crest.x + 7, crest.y + lipHeight * 0.85, foamWidth * 0.38, lipHeight * 0.85, 0.25, 0, Math.PI * 2);
        ctx.fill();
      }

      // 2. Bright Curling Whitewater Foam Crest
      const foamGrad = ctx.createLinearGradient(
        crest.x - foamWidth * 0.45,
        crest.y - 2,
        crest.x + foamWidth * 0.9,
        crest.y + lipHeight * 1.4
      );
      foamGrad.addColorStop(0, 'rgba(255, 255, 255, 0.96)');
      foamGrad.addColorStop(0.65, 'rgba(224, 242, 254, 0.82)');
      foamGrad.addColorStop(1, 'rgba(125, 211, 252, 0)');

      ctx.fillStyle = foamGrad;
      ctx.beginPath();
      ctx.ellipse(crest.x + 4, crest.y + 1.5, foamWidth * 0.56, Math.max(2.2, lipHeight * 0.75), 0.14, 0, Math.PI * 2);
      ctx.fill();

      // 3. Tumbling Whitewater Bubbles on the Breaking Shoulder (when height >= 1.2 ft)
      if (state.heightFt >= 1.2) {
        for (let b = 0; b < 5; b++) {
          const bx = crest.x + 2 + b * 4.2 + Math.sin(this.phase * 3 + b) * 1.5;
          const by = crest.y + 1.5 + Math.cos(this.phase * 2.5 + b) * 1.8;
          const br = Math.max(1.3, (5 - b) * 0.65 * Math.min(1.6, state.heightFt * 0.45));
          ctx.fillStyle = 'rgba(255, 255, 255, 0.82)';
          ctx.beginPath();
          ctx.arc(bx, by, br, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // 4. Offshore Groomed Spindrift Spray Plumes blowing off the back of the lip
      if ((isOffshore || state.heightFt >= 1.5) && state.heightFt >= 1.0) {
        const sprayCount = isOffshore ? 10 : 5;
        for (let pIdx = 0; pIdx < sprayCount; pIdx++) {
          const p = this.sprayParticles[(idx * 7 + pIdx) % this.sprayParticles.length];
          const t = (this.phase * p.speed + p.seed + idx * 0.25) % 1;
          const dirSign = isOnshore ? 1 : -1;
          const sx = crest.x + dirSign * t * 26 + p.offsetX * 0.12;
          const sy = crest.y - t * 13 - Math.sin(t * Math.PI) * 5.5;
          const alpha = (1 - t) * (isOffshore ? 0.78 : 0.48);
          ctx.fillStyle = `rgba(224, 242, 254, ${alpha.toFixed(2)})`;
          ctx.beginPath();
          ctx.arc(sx, sy, p.size * (1 - t * 0.35), 0, Math.PI * 2);
          ctx.fill();
        }
      }
    });

    ctx.restore();
  }

  _drawShorelineSandbarAndSwash(ctx, w, h, ampPx, k, phase, chopFactor) {
    // Foreground whitewater swash ripple
    this._drawShoalingWaveLayer(ctx, {
      w,
      h,
      baseY: h - 12,
      amp: Math.max(2.2, ampPx * 0.24),
      k: k * 1.28,
      phase: phase * 1.18 + 2.6,
      chopFactor: chopFactor * 0.4,
      steepness: 0.16,
      shoalGain: 0.1,
      fillTop: 'rgba(125, 211, 252, 0.24)',
      fillMid: 'rgba(14, 116, 144, 0.35)',
      fillBottom: 'rgba(4, 12, 24, 0.96)',
      strokeColor: 'rgba(224, 242, 254, 0.45)',
      returnCrests: false,
    });
  }
}
