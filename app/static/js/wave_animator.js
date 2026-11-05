/**
 * app/static/js/wave_animator.js
 * Upgraded HTML5 Canvas Coastal Wave & Shoaling Simulator for Lafayette Wave Enthusiasts:
 * - Supports multiple simultaneous instances (one live wave tank per beach card: Holly Beach & Grand Isle)
 * - Simulates real left-to-right shoaling over a sloping coastal sandbar:
 *     * Deep Gulf groundswell enters on the left, steepens over the sandbar, and pitches/breaks on the right
 * - Translucent backlit emerald-cyan wave faces with subsurface light refraction
 * - Feathered, organic whitecaps with analytical continuous crest tracking (zero jumping or chop jitter)
 * - Delicate trailing sea foam lacing, tumbling frothy aerated lip rollers, and silky wind spindrift
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
    this.sprayParticles = Array.from({ length: 24 }, (_, i) => ({
      seed: i / 24,
      offsetX: (Math.random() - 0.5) * 16,
      offsetY: Math.random() * 8,
      size: 1.0 + Math.random() * 1.8,
      speed: 0.8 + Math.random() * 0.4,
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
      this.currentState.periodSec += (this.targetState.periodSec - this.currentState.periodSec) * lerp;
      this.currentState.windSpeedMph += (this.targetState.windSpeedMph - this.currentState.windSpeedMph) * lerp;
      this.currentState.surfScore += (this.targetState.surfScore - this.currentState.surfScore) * lerp;
      this.currentState.windRegime = this.targetState.windRegime;
      this.currentState.swellCompass = this.targetState.swellCompass;
      this.currentState.spotName = this.targetState.spotName;
      this.currentState.timeLabel = this.targetState.timeLabel;

      const speedFactor = 9.0 / Math.max(4.2, this.currentState.periodSec);
      this.phase += dt * 2.15 * speedFactor;

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
    const visualFt = Math.pow(state.heightFt, 0.88) * 1.18;
    const ampPx = Math.max(4.5, Math.min(h * 0.36, visualFt * pxPerFoot));
    const wavelengthPx = Math.max(95, Math.min(270, state.periodSec * 21));
    const k = (2 * Math.PI) / wavelengthPx;
    const chopFactor = isGlassy ? 0.02 : isOffshore ? 0.05 : isOnshore ? 0.22 : 0.10;

    // 4. Deep Horizon Swell Layer
    this._drawShoalingWaveLayer(ctx, {
      w,
      h,
      baseY: baseWaterY - 7,
      amp: ampPx * 0.52,
      k: k * 0.82,
      phase: phase * 0.68 + 2.1,
      chopFactor: chopFactor * 0.35,
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
      amp: ampPx * 0.74,
      k: k * 0.92,
      phase: phase * 0.86 + 0.9,
      chopFactor: chopFactor * 0.55,
      steepness: 0.28,
      shoalGain: 0.26,
      fillTop: 'rgba(6, 182, 212, 0.52)',
      fillMid: 'rgba(3, 105, 161, 0.75)',
      fillBottom: 'rgba(6, 20, 39, 0.94)',
      strokeColor: 'rgba(103, 232, 249, 0.38)',
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
      steepness: isOffshore ? 0.44 : 0.34,
      shoalGain: 0.40,
      fillTop: state.surfScore >= 52 ? 'rgba(45, 212, 191, 0.88)' : 'rgba(34, 211, 238, 0.80)',
      fillMid: 'rgba(2, 132, 199, 0.88)',
      fillBottom: 'rgba(4, 15, 31, 0.98)',
      strokeColor: '#a5f3fc',
      returnCrests: true,
    });

    // 7. Organic Feathered Whitecaps, Curling Lip & Trailing Sea Foam Lacing
    this._drawBreakingFoamAndWhitecaps(ctx, crestPoints, state, ampPx, w, h, baseWaterY, isOffshore, isOnshore);

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

  _getWaveProfile(x, w, baseY, amp, k, phase, chopFactor, steepness, shoalGain) {
    const normX = Math.max(0, Math.min(1, x / w));
    const shoalMultiplier = 0.82 + shoalGain * Math.sin(normX * Math.PI * 0.85);
    const localAmp = amp * shoalMultiplier;

    // Skew wave forward as it shoals over the shallow coastal sandbar
    const theta = k * x - phase + normX * 0.35;
    const primary =
      Math.sin(theta) +
      steepness * Math.sin(2 * theta + 0.45) +
      steepness * 0.32 * Math.sin(3 * theta + 0.9);

    // Harmonically tuned surface ripples (phase-synchronized with the swell to eliminate jumpy noise)
    const smoothChop =
      chopFactor *
      (0.30 * Math.sin(2.0 * theta + 0.3) + 0.12 * Math.cos(3.0 * theta - 0.2));

    const y = baseY - localAmp * (primary * 0.74 + smoothChop);
    return { y, localAmp, normX, theta, primary };
  }

  _drawShoalingWaveLayer(
    ctx,
    { w, h, baseY, amp, k, phase, chopFactor, steepness, shoalGain, fillTop, fillMid, fillBottom, strokeColor, returnCrests }
  ) {
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(0, h);

    // Step by 2 pixels for smooth, silky rendering
    for (let x = 0; x <= w; x += 2) {
      const { y } = this._getWaveProfile(x, w, baseY, amp, k, phase, chopFactor, steepness, shoalGain);
      ctx.lineTo(x, y);
    }
    const rightEdge = this._getWaveProfile(w, w, baseY, amp, k, phase, chopFactor, steepness, shoalGain);
    ctx.lineTo(w, rightEdge.y);
    ctx.lineTo(w, h);
    ctx.closePath();

    const grad = ctx.createLinearGradient(0, baseY - amp * 1.25, 0, h);
    grad.addColorStop(0, fillTop);
    grad.addColorStop(0.42, fillMid);
    grad.addColorStop(1, fillBottom);
    ctx.fillStyle = grad;
    ctx.fill();

    const crests = [];
    if (returnCrests) {
      // Analytical continuous crest tracking:
      // The primary wave crest occurs at peakTheta ≈ 0.773 rad.
      // Continuous x coordinate moves smoothly with (phase + peakTheta) / kEff,
      // completely eliminating discrete 3px sampling jumps and pop-in flicker!
      const kEff = k + 0.35 / w;
      const peakTheta = 0.773;
      const minX = -40;
      const maxX = w + 40;
      const mStart = Math.floor((minX * kEff - phase - peakTheta) / (2 * Math.PI));
      const mEnd = Math.ceil((maxX * kEff - phase - peakTheta) / (2 * Math.PI));

      for (let m = mStart; m <= mEnd; m++) {
        const xCrest = (phase + peakTheta + 2 * Math.PI * m) / kEff;
        if (xCrest >= -30 && xCrest <= w + 30) {
          const profile = this._getWaveProfile(xCrest, w, baseY, amp, k, phase, chopFactor, steepness, shoalGain);

          // Smooth cosine/linear envelope so whitecaps enter and exit the canvas smoothly
          const enterFade = Math.max(0, Math.min(1, (xCrest + 25) / 50));
          const exitFade = Math.max(0, Math.min(1, (w + 25 - xCrest) / 50));
          const visibility = enterFade * exitFade;

          crests.push({
            x: xCrest,
            y: profile.y,
            localAmp: profile.localAmp,
            normX: profile.normX,
            visibility,
          });
        }
      }

      // Sunlit subsurface caustic reflections under each continuous wave face
      if (crests.length > 0) {
        ctx.strokeStyle = 'rgba(165, 243, 252, 0.20)';
        ctx.lineWidth = 1.2;
        crests.forEach((c) => {
          if (c.visibility > 0.1) {
            ctx.save();
            ctx.globalAlpha = c.visibility * 0.85;
            ctx.beginPath();
            ctx.moveTo(c.x - 18, c.y + 4);
            ctx.quadraticCurveTo(c.x + 6, c.y + c.localAmp * 0.42, c.x + 24, c.y + c.localAmp * 0.82);
            ctx.stroke();
            ctx.restore();
          }
        });
      }
    }

    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = returnCrests ? 2.2 : 1.4;
    ctx.stroke();
    ctx.restore();

    return crests;
  }

  _drawBreakingFoamAndWhitecaps(ctx, crests, state, ampPx, w, h, baseWaterY, isOffshore, isOnshore) {
    if (!crests.length) return;
    ctx.save();

    crests.forEach((crest, idx) => {
      const vis = crest.visibility ?? 1.0;
      if (vis <= 0.02) return;

      // As waves shoal over the sandbar (normX increasing), the whitecap curls and expands
      const shoalingScale = 0.70 + crest.normX * 0.55;
      const baseFoamWidth = Math.max(16, Math.min(54, (12 + state.heightFt * 7.2) * shoalingScale));
      const foamWidth = baseFoamWidth * vis;
      const lipThickness = Math.max(2.2, Math.min(9.5, crest.localAmp * 0.22 + state.heightFt * 0.85));

      ctx.save();
      ctx.globalAlpha = vis;

      // 1. Backlit Under-Lip Pocket Shadow (Curling Barrel Depth)
      if (state.heightFt >= 0.8 && crest.normX > 0.25) {
        const shadowDepth = lipThickness * (0.85 + crest.normX * 0.4);
        const shadowGrad = ctx.createLinearGradient(
          crest.x,
          crest.y,
          crest.x + foamWidth * 0.35,
          crest.y + shadowDepth * 1.4
        );
        shadowGrad.addColorStop(0, 'rgba(2, 48, 82, 0.60)');
        shadowGrad.addColorStop(0.5, 'rgba(2, 80, 130, 0.35)');
        shadowGrad.addColorStop(1, 'rgba(2, 132, 199, 0)');

        ctx.fillStyle = shadowGrad;
        ctx.beginPath();
        ctx.moveTo(crest.x - foamWidth * 0.22, crest.y + 1);
        ctx.quadraticCurveTo(
          crest.x + foamWidth * 0.25,
          crest.y + shadowDepth * 1.35,
          crest.x + foamWidth * 0.58,
          crest.y + shadowDepth * 0.55
        );
        ctx.quadraticCurveTo(crest.x + foamWidth * 0.22, crest.y + 2, crest.x - foamWidth * 0.22, crest.y + 1);
        ctx.fill();
      }

      // 2. Feathered, Organic Curled Whitecap Mantle (Smooth Hydrodynamic Cap)
      const capLeftX = crest.x - foamWidth * 0.48;
      const capRightX = crest.x + foamWidth * 0.52;
      const capPeakY = crest.y - lipThickness * 0.38;
      const capLipY = crest.y + lipThickness * 0.78;

      const mantleGrad = ctx.createLinearGradient(capLeftX, capPeakY, capRightX, capLipY);
      mantleGrad.addColorStop(0, 'rgba(255, 255, 255, 0.05)');
      mantleGrad.addColorStop(0.25, 'rgba(255, 255, 255, 0.86)');
      mantleGrad.addColorStop(0.55, 'rgba(255, 255, 255, 0.98)');
      mantleGrad.addColorStop(0.82, 'rgba(224, 242, 254, 0.86)');
      mantleGrad.addColorStop(1, 'rgba(186, 230, 253, 0.0)');

      ctx.fillStyle = mantleGrad;
      ctx.beginPath();
      // Back wave shoulder taper
      ctx.moveTo(capLeftX, crest.y + 1);
      // Fluid upward arch over the curling wave peak
      ctx.quadraticCurveTo(
        crest.x - foamWidth * 0.05,
        capPeakY,
        crest.x + foamWidth * 0.20,
        capPeakY + lipThickness * 0.18
      );
      // Pitching curl-lip on the face
      ctx.quadraticCurveTo(
        capRightX,
        crest.y + lipThickness * 0.32,
        capRightX - foamWidth * 0.08,
        capLipY
      );
      // Return along the contoured wave face
      ctx.quadraticCurveTo(
        crest.x + foamWidth * 0.14,
        crest.y + lipThickness * 0.42,
        capLeftX,
        crest.y + 1
      );
      ctx.closePath();
      ctx.fill();

      // 3. Luminous Curling Lip Spine Highlight
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.95)';
      ctx.lineWidth = Math.max(1.2, Math.min(2.4, 0.8 + state.heightFt * 0.38));
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(crest.x - foamWidth * 0.32, crest.y);
      ctx.quadraticCurveTo(
        crest.x + foamWidth * 0.12,
        capPeakY + 0.6,
        crest.x + foamWidth * 0.38,
        crest.y + lipThickness * 0.35
      );
      ctx.stroke();

      // 4. Delicate Trailing Sea Foam Lacing (Marbled Filigree Lines)
      if (state.heightFt >= 0.8) {
        const lacingCount = Math.min(4, Math.max(2, Math.floor(state.heightFt + 1)));
        ctx.lineWidth = 1.0;
        for (let l = 0; l < lacingCount; l++) {
          const lFrac = (l + 1) / (lacingCount + 1);
          const lxStart = crest.x + foamWidth * (0.04 + lFrac * 0.32);
          const lyStart = crest.y + lipThickness * (0.22 + lFrac * 0.36);
          const lxEnd = lxStart + 11 + l * 4;
          const lyEnd = lyStart + crest.localAmp * (0.32 + lFrac * 0.28);
          const lCtrlX = lxStart + 5;
          const lCtrlY = lyStart + (lyEnd - lyStart) * 0.38;

          const lacingAlpha = (0.34 - l * 0.06);
          ctx.strokeStyle = `rgba(240, 249, 255, ${lacingAlpha.toFixed(2)})`;
          ctx.beginPath();
          ctx.moveTo(lxStart, lyStart);
          ctx.quadraticCurveTo(lCtrlX, lCtrlY, lxEnd, lyEnd);
          ctx.stroke();
        }
      }

      // 5. Smoothly Tumbling Froth Rollers (Phase-Locked, Continuous Motion)
      if (state.heightFt >= 1.1 && crest.normX > 0.38) {
        const puffCount = Math.min(5, Math.max(2, Math.floor(state.heightFt * 1.5)));
        for (let p = 0; p < puffCount; p++) {
          // Continuous parametric progression (u from 0 to 1) along the tumbling curl foot
          const u = ((this.phase * 0.55 + p * 0.22 + idx * 0.15) % 1);
          const px = crest.x + foamWidth * 0.14 + u * (foamWidth * 0.36);
          const py = crest.y + lipThickness * 0.42 + Math.sin(u * Math.PI) * (lipThickness * 0.52);
          const pr = (1.6 + (1 - u) * 2.0) * Math.min(1.4, state.heightFt * 0.48);
          const pAlpha = Math.sin(u * Math.PI) * 0.72;

          if (pAlpha > 0.05) {
            const puffGrad = ctx.createRadialGradient(px, py, 0, px, py, pr);
            puffGrad.addColorStop(0, `rgba(255, 255, 255, ${pAlpha.toFixed(2)})`);
            puffGrad.addColorStop(0.65, `rgba(224, 242, 254, ${(pAlpha * 0.6).toFixed(2)})`);
            puffGrad.addColorStop(1, 'rgba(224, 242, 254, 0)');

            ctx.fillStyle = puffGrad;
            ctx.beginPath();
            ctx.arc(px, py, pr, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }

      // 6. Silky Wind-Groomed Spindrift Spray (Feathered Mist Plumes)
      if ((isOffshore || state.heightFt >= 1.4) && state.heightFt >= 0.85) {
        const sprayCount = isOffshore ? 6 : 3;
        for (let s = 0; s < sprayCount; s++) {
          const u = ((this.phase * 0.75 + s * 0.28 + idx * 0.16) % 1);
          const dir = isOnshore ? 1 : -1;
          const sx = crest.x - foamWidth * 0.08 + dir * u * 22;
          const sy = crest.y - lipThickness * 0.18 - u * 11 - Math.sin(u * Math.PI) * 4.0;
          const sr = 1.1 + (1 - u) * 1.6;
          const sAlpha = Math.sin(u * Math.PI) * (isOffshore ? 0.62 : 0.32);

          if (sAlpha > 0.04) {
            ctx.fillStyle = `rgba(240, 249, 255, ${sAlpha.toFixed(2)})`;
            ctx.beginPath();
            ctx.arc(sx, sy, sr, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }

      ctx.restore();
    });

    ctx.restore();
  }

  _drawShorelineSandbarAndSwash(ctx, w, h, ampPx, k, phase, chopFactor) {
    // Foreground whitewater swash ripple
    this._drawShoalingWaveLayer(ctx, {
      w,
      h,
      baseY: h - 12,
      amp: Math.max(2.2, ampPx * 0.22),
      k: k * 1.28,
      phase: phase * 1.18 + 2.6,
      chopFactor: chopFactor * 0.3,
      steepness: 0.16,
      shoalGain: 0.1,
      fillTop: 'rgba(125, 211, 252, 0.22)',
      fillMid: 'rgba(14, 116, 144, 0.32)',
      fillBottom: 'rgba(4, 12, 24, 0.96)',
      strokeColor: 'rgba(224, 242, 254, 0.40)',
      returnCrests: false,
    });
  }
}
