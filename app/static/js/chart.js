/**
 * app/static/js/chart.js
 * Chart.js 4.4.1 48-Hour Breaking Wave Height, Swell Period & Surf Quality Timeline.
 */

export class ForecastChartController {
  constructor(canvasId) {
    this.canvasId = canvasId;
    this.chartInstance = null;
  }

  render(hourlyPoints = []) {
    const canvas = document.getElementById(this.canvasId);
    if (!canvas || typeof window.Chart === 'undefined') return;

    // Sample every 2 hours if 48 points to keep X-axis crisp, or use up to 24 slices
    const points = Array.isArray(hourlyPoints) ? hourlyPoints.slice(0, 48) : [];
    const labels = points.map((pt) => {
      const d = new Date(pt.timestamp);
      if (Number.isNaN(d.getTime())) return '';
      return d.toLocaleTimeString([], { weekday: 'short', hour: 'numeric' });
    });

    const waveHeights = points.map((pt) => pt.breaking_height_ft ?? 0);
    const periods = points.map((pt) => pt.dominant_period_sec ?? 0);
    const scores = points.map((pt) => pt.surf_score ?? 0);

    const pointColors = scores.map((s) => {
      if (s >= 80) return '#a855f7';
      if (s >= 56) return '#10b981';
      if (s >= 31) return '#f59e0b';
      return '#ef4444';
    });

    if (this.chartInstance) {
      this.chartInstance.destroy();
    }

    const ctx = canvas.getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, 200);
    gradient.addColorStop(0, 'rgba(56, 189, 248, 0.42)');
    gradient.addColorStop(1, 'rgba(56, 189, 248, 0.02)');

    this.chartInstance = new window.Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Breaking Wave Height (ft)',
            data: waveHeights,
            borderColor: '#38bdf8',
            backgroundColor: gradient,
            borderWidth: 2.5,
            pointBackgroundColor: pointColors,
            pointBorderColor: '#070d19',
            pointRadius: 3.5,
            pointHoverRadius: 5.5,
            fill: true,
            tension: 0.35,
            yAxisID: 'yWave',
          },
          {
            label: 'Swell Period (s)',
            data: periods,
            borderColor: '#2dd4bf',
            borderDash: [4, 4],
            borderWidth: 1.8,
            pointRadius: 0,
            fill: false,
            tension: 0.35,
            yAxisID: 'yPeriod',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        onClick: (evt, activeEls) => {
          if (activeEls && activeEls.length > 0) {
            const idx = activeEls[0].index;
            const slider = document.getElementById('forecast-time-slider');
            if (slider) {
              slider.value = String(idx);
              slider.dispatchEvent(new Event('input', { bubbles: true }));
            }
          }
        },
        interaction: {
          mode: 'index',
          intersect: false,
        },
        plugins: {
          legend: {
            labels: {
              color: '#94a3b8',
              font: { family: "'JetBrains Mono', monospace", size: 11 },
              boxWidth: 12,
            },
          },
          tooltip: {
            backgroundColor: 'rgba(11, 19, 35, 0.95)',
            titleColor: '#f8fafc',
            bodyColor: '#cbd5e1',
            borderColor: 'rgba(56, 189, 248, 0.4)',
            borderWidth: 1,
            callbacks: {
              afterBody: (items) => {
                const idx = items[0]?.dataIndex;
                const pt = points[idx];
                if (!pt) return [];
                return [
                  `Surf Score: ${pt.surf_score}/100 (${pt.decision})`,
                  `Wind: ${Math.round(pt.wind_speed_mph)} mph ${pt.wind_direction_cardinal} (${pt.wind_regime})`,
                  `Tide: ${pt.tide_status || 'Rising'} (${pt.tide_height_ft ?? '--'} ft)`,
                ];
              },
            },
          },
        },
        scales: {
          x: {
            ticks: {
              color: '#64748b',
              maxTicksLimit: 8,
              font: { family: "'JetBrains Mono', monospace", size: 10 },
            },
            grid: { color: 'rgba(148, 163, 184, 0.08)' },
          },
          yWave: {
            type: 'linear',
            position: 'left',
            suggestedMin: 0,
            suggestedMax: 6,
            title: {
              display: true,
              text: 'Wave (ft)',
              color: '#38bdf8',
              font: { family: "'JetBrains Mono', monospace", size: 10 },
            },
            ticks: {
              color: '#94a3b8',
              font: { family: "'JetBrains Mono', monospace", size: 10 },
            },
            grid: { color: 'rgba(148, 163, 184, 0.1)' },
          },
          yPeriod: {
            type: 'linear',
            position: 'right',
            suggestedMin: 3,
            suggestedMax: 12,
            title: {
              display: true,
              text: 'Period (s)',
              color: '#2dd4bf',
              font: { family: "'JetBrains Mono', monospace", size: 10 },
            },
            ticks: {
              color: '#2dd4bf',
              font: { family: "'JetBrains Mono', monospace", size: 10 },
            },
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
  }
}
