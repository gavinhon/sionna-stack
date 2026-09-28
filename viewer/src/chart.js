// Minimal line chart on a <canvas> (no charting library): total path gain vs time for each receiver,
// dots where the receiver has line of sight, and a vertical cursor at the current timeline time.

// 12 receiver colours (same list in scripts/export_geo.py); the wide-area run has 12 receivers
export const RX_COLORS = ["#4fb3ff", "#ffb14f", "#7ee07e", "#ff6b9a", "#c49bff", "#f5e663",
  "#46e0d0", "#ff8a5c", "#b8e05a", "#e07bd8", "#9aa7ff", "#ffd1a1"];

export function drawChart(canvas, { times, series, los }, cursorTime, band) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  const pad = { l: 44, r: 10, t: 18, b: 26 };
  ctx.clearRect(0, 0, W, H);
  const vals = Object.values(series).flat().filter((v) => v !== null);
  if (!vals.length) return;
  let ymin = Math.floor(Math.min(...vals) / 10) * 10, ymax = Math.ceil(Math.max(...vals) / 10) * 10;
  if (ymax - ymin < 20) ymin = ymax - 20;
  const t0 = times[0], t1 = times[times.length - 1] || 1;
  const X = (t) => pad.l + ((t - t0) / (t1 - t0 || 1)) * (W - pad.l - pad.r);
  const Y = (v) => pad.t + (1 - (v - ymin) / (ymax - ymin)) * (H - pad.t - pad.b);

  ctx.font = "11px system-ui, sans-serif";
  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  ctx.fillStyle = "#9aa4b2";
  for (let v = ymin; v <= ymax; v += 10) {
    ctx.beginPath(); ctx.moveTo(pad.l, Y(v)); ctx.lineTo(W - pad.r, Y(v)); ctx.stroke();
    ctx.fillText(`${v}`, 6, Y(v) + 4);
  }
  for (let t = Math.ceil(t0 / 5) * 5; t <= t1; t += 5) ctx.fillText(`${t}s`, X(t) - 8, H - 8);
  ctx.fillText(`dB, ${band}`, pad.l, 12);

  Object.entries(series).forEach(([rx, ys], k) => {
    const c = RX_COLORS[k % RX_COLORS.length];
    ctx.strokeStyle = c; ctx.lineWidth = 1.8; ctx.beginPath();
    let pen = false;
    ys.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      if (!pen) { ctx.moveTo(X(times[i]), Y(v)); pen = true; } else ctx.lineTo(X(times[i]), Y(v));
    });
    ctx.stroke();
    ctx.fillStyle = c;
    ys.forEach((v, i) => { if (v !== null && los[rx][i]) { ctx.beginPath(); ctx.arc(X(times[i]), Y(v), 2.5, 0, 7); ctx.fill(); } });
    ctx.fillText(rx, W - pad.r - 80, pad.t + 12 + 13 * k);
  });

  if (cursorTime !== null) {
    ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(X(cursorTime), pad.t); ctx.lineTo(X(cursorTime), H - pad.b); ctx.stroke();
    ctx.setLineDash([]);
  }
}
