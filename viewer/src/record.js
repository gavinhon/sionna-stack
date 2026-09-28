// Recording mode: plays scripted scenarios in the viewer and captures frames for the documentation GIFs.
//   http://localhost:5180/?record=all              every scenario below
//   http://localhost:5180/?record=bands,height     selected scenarios
// Frames (800x450 PNG, caption + legend drawn in) are POSTed to the dev server, which writes them to
// outputs/captures/<scenario>/ (vite.config.js: captureSinkPlugin). Then:
//   uv run scripts/make_gifs.py            -> docs/img/viewer_<scenario>.gif
// The camera is driven directly and every frame waits until map tiles, models and entities are loaded, so the
// result does not depend on network speed. Only CesiumJS rendering is captured (not the side panel).
import * as Cesium from "cesium";

const W = 800, H = 450;
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rad = Cesium.Math.toRadians;

const RUN_LABEL = {
  sg_raffles_place: "Reference transmitter (omni, 12 m)",
  sg_macro: "3-sector rooftop macro (47 m, 46 dBm)",
  sg_smallcell_mmwave: "mmWave lamp-post small cell (6 m, 8x8 beams)",
};

export const SCENARIOS = {
  overview: { experiment: "sg_raffles_place", kind: "orbit", band: "3p5GHz", frames: 48, ms: 110 },
  bands: { experiment: "sg_raffles_place", kind: "bands", ms: 1200 },
  macro_bands: { experiment: "sg_macro", kind: "bands", ms: 1200 },
  shells: { experiment: "sg_raffles_place", kind: "shells", perBand: 6, ms: 140 },
  smallcell_shells: { experiment: "sg_smallcell_mmwave", kind: "shells", perBand: 14, ms: 140 },
  height: { experiment: "sg_raffles_place", kind: "height", band: "3p5GHz", ms: 200 },
  moving: { experiment: "sg_raffles_place", kind: "moving", band: "3p5GHz", ms: 160 },
};

export async function record(state, index, which) {
  const names = !which || which === "all" ? Object.keys(SCENARIOS) : which.split(",").map((s) => s.trim());
  window.recordDone = false;
  for (const name of names) {
    const sc = SCENARIOS[name];
    if (!sc) { console.warn(`record: unknown scenario ${name}`); continue; }
    const entry = index.exports.find((e) => e.experiment === sc.experiment);
    if (!entry) { console.warn(`record: no exported run for ${sc.experiment}`); continue; }
    window.recordStatus = `${name}: loading`;
    const rec = new Recorder(state, name);
    await state.api.setMode("static");
    await state.api.loadRun(entry.path);
    state.api.setDark(false, false); // scenarios that want the dark view switch it on themselves
    resetLook(state);
    await KINDS[sc.kind](state, sc, rec);
    await rec.finish(sc);
    console.log(`record: ${name} done (${rec.i} frames)`);
  }
  resetLook(state);
  state.viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
  window.recordStatus = "done";
  window.recordDone = true;
}

// ------------------------------------------------------------------------------------------- scenarios
const KINDS = {
  // one orbit around the site: ground coverage + ray paths
  async orbit(state, sc, rec) {
    await state.api.setBand(sc.band);
    const b = band(state);
    buildingAlpha(state, 0.25);
    for (let k = 0; k < sc.frames; k++) {
      look(state, 15 + (360 * k) / sc.frames, -52, 950);
      await settle(state, k === 0 ? 800 : 60);
      await rec.grab([`${runLabel(state)} · ${b.label}`,
        "coloured ground = signal at 1.5 m · lines = ray paths to the receivers"], sc.ms, { legend: true });
    }
  },

  // same camera, every frequency band
  async bands(state, sc, rec) {
    look(state, 15, -62, 950);
    for (const b of state.exp.bands) {
      await state.api.setBand(b.slug);
      await settle(state, 700);
      await rec.grab([`${runLabel(state)} · ${b.label}`,
        "same place and transmitter, only the frequency changes"], sc.ms, { legend: true });
    }
  },

  // coverage shell (middle level) per band while the camera turns
  async shells(state, sc, rec) {
    const levels = state.exp.volume.shell_levels.slice().sort((a, c) => a - c);
    const lev = levels[Math.floor(levels.length / 2)];
    for (const l of levels) state.shellShow[l] = l === lev;
    const unit = state.exp.volume.metric.includes("dBm") ? "dBm" : "dB";
    state.api.setDark(true, false); // the viewer's "dark view": only the signal is coloured
    const bands = state.exp.volume.bands;
    const total = bands.length * sc.perBand;
    let k = 0;
    for (const vb of bands) {
      await state.api.setBand(vb.slug);
      const label = state.exp.bands.find((b) => b.slug === vb.slug).label;
      for (let j = 0; j < sc.perBand; j++, k++) {
        look(state, 15 + (360 * k) / total, -32, 950);
        await settle(state, j === 0 ? 800 : 60);
        await rec.grab([`${runLabel(state)} · ${label}`,
          `3-D coverage shell: inside the surface the signal is stronger than ${lev} ${unit}`], sc.ms);
      }
    }
    state.api.setDark(false, false);
  },

  // the signal map floating at every computed height, 1.5 m -> 150 m
  async height(state, sc, rec) {
    $("show-rm").checked = false;
    for (const l of Object.keys(state.shellShow)) state.shellShow[l] = false;
    await state.api.setBand(sc.band);
    Object.values(state.pathSources).forEach((ds) => (ds.show = false));
    const vb = state.exp.volume.bands.find((b) => b.slug === sc.band);
    $("show-layer").checked = true;
    buildingAlpha(state, 0.25);
    look(state, 15, -36, 950);
    for (let k = 0; k < vb.heights_m.length; k++) {
      $("layer-h").value = String(k);
      state.api.updateLayer(); // creates / moves the rectangle
      // swap in an already loaded image so the frame never shows a half-loaded texture
      // (onload, not img.decode(): decode() never settles while the browser tab is in the background)
      const img = new Image();
      await new Promise((r) => { img.onload = img.onerror = r; img.src = `data/${state.run}/${vb.frames[k]}`; });
      state.layerEntity.rectangle.material = new Cesium.ImageMaterialProperty({ image: img, transparent: true });
      await settle(state, k === 0 ? 800 : 150);
      const hold = k === vb.heights_m.length - 1 ? 1500 : sc.ms;
      await rec.grab([`${runLabel(state)} · ${band(state).label}`,
        `signal map at ${vb.heights_m[k]} m above the ground (1.5 m -> 150 m)`], hold, { legend: true });
    }
    $("show-layer").checked = false;
    $("show-rm").checked = true;
    resetLook(state);
    state.api.updateLayer();
  },

  // the moving transmitter, one frame per simulated step
  async moving(state, sc, rec) {
    await state.api.setBand(sc.band);
    await state.api.setMode("mobility");
    const v = state.viewer, m = state.exp.mobility;
    const epoch = Cesium.JulianDate.fromIso8601(m.epoch);
    look(state, 15, -62, 950);
    for (let i = 0; i < m.times_s.length; i++) {
      v.clock.currentTime = Cesium.JulianDate.addSeconds(epoch, m.times_s[i] + 0.5 * m.dt_s, new Cesium.JulianDate());
      await settle(state, i === 0 ? 800 : 120);
      await rec.grab([`Moving transmitter (red) · ${band(state).label} · t = ${m.times_s[i].toFixed(1)} s`,
        "the coverage map and ray paths are recomputed at every position"], sc.ms, { legend: true });
    }
    await state.api.setMode("static");
  },
};

// ------------------------------------------------------------------------------------------- helpers
const band = (state) => state.exp.bands.find((b) => b.slug === state.band);
const runLabel = (state) => RUN_LABEL[state.exp.experiment] || state.exp.experiment;

function look(state, headingDeg, pitchDeg, range) {
  state.viewer.camera.lookAt(state.origin, new Cesium.HeadingPitchRange(rad(headingDeg), rad(pitchDeg), range));
}

function buildingAlpha(state, a) {
  if (state.model) state.model.color = Cesium.Color.WHITE.withAlpha(a);
}

function resetLook(state) {
  const b = $("bld-alpha");
  if (state.model) state.model.color = Cesium.Color.WHITE.withAlpha(Number(b.value));
}

// render until tiles, models and entities are all loaded (and at least minMs has passed)
async function settle(state, minMs = 100, maxMs = 30000) {
  const v = state.viewer;
  const t0 = performance.now();
  let ok = 0;
  while (performance.now() - t0 < maxMs) {
    v.render();
    const ready = v.scene.globe.tilesLoaded && v.dataSourceDisplay.ready !== false &&
      (!state.model || state.model.ready) && state.shellModels.every((m) => m.ready);
    ok = ready ? ok + 1 : 0;
    if (ok >= 3 && performance.now() - t0 >= minMs) return;
    await sleep(30);
  }
  console.warn("record: gave up waiting for tiles/models");
}

class Recorder {
  constructor(state, name) {
    Object.assign(this, { state, name, i: 0, durations: [] });
    this.canvas = document.createElement("canvas");
    this.canvas.width = W; this.canvas.height = H;
  }

  async grab(lines, ms, { legend = false } = {}) {
    const v = this.state.viewer;
    v.render();
    const src = v.scene.canvas;
    let cw = src.width, ch = Math.round((src.width * H) / W); // centre crop to 16:9
    if (ch > src.height) { ch = src.height; cw = Math.round((src.height * W) / H); }
    const g = this.canvas.getContext("2d");
    g.drawImage(src, (src.width - cw) / 2, (src.height - ch) / 2, cw, ch, 0, 0, W, H);
    // caption
    g.fillStyle = "rgba(15,18,24,0.78)";
    g.fillRect(0, 0, W, 50);
    g.fillStyle = "#fff"; g.font = "600 17px system-ui, sans-serif";
    g.fillText(lines[0], 12, 22);
    g.fillStyle = "#cfd6df"; g.font = "13px system-ui, sans-serif";
    g.fillText(lines[1] || "", 12, 41);
    // credit
    const credit = "Sionna RT + CesiumJS · map © OpenStreetMap contributors";
    g.font = "11px system-ui, sans-serif";
    const cwid = g.measureText(credit).width;
    g.fillStyle = "rgba(15,18,24,0.7)"; g.fillRect(W - cwid - 14, H - 20, cwid + 14, 20);
    g.fillStyle = "#e8ecf1"; g.fillText(credit, W - cwid - 7, H - 6);
    if (legend) this.drawLegend(g);

    const blob = await new Promise((r) => this.canvas.toBlob(r, "image/png"));
    const file = `f${String(this.i).padStart(4, "0")}.png`;
    await fetch(`/__capture/${this.name}/${file}`, { method: "POST", body: blob });
    this.durations.push(ms);
    this.i += 1;
    window.recordStatus = `${this.name}: frame ${this.i}`;
  }

  drawLegend(g) {
    const lg = this.state.exp.legend;
    const x = 10, y = H - 48, w = 200, h = 10;
    g.fillStyle = "rgba(15,18,24,0.7)"; g.fillRect(x - 6, y - 18, w + 12, 50);
    const grad = g.createLinearGradient(x, 0, x + w, 0);
    for (const [s, c] of lg.stops) grad.addColorStop(s, `rgb(${c[0]},${c[1]},${c[2]})`);
    g.fillStyle = grad; g.fillRect(x, y, w, h);
    g.fillStyle = "#e8ecf1"; g.font = "11px system-ui, sans-serif";
    g.fillText(lg.metric, x, y - 5);
    g.fillText(`${Math.round(lg.vmin)} (weak)`, x, y + h + 13);
    const hi = `${Math.round(lg.vmax)} (strong)`;
    g.fillText(hi, x + w - g.measureText(hi).width, y + h + 13);
  }

  async finish(sc) {
    const meta = { scenario: this.name, experiment: sc.experiment, run: this.state.run, width: W, height: H,
      frames: this.durations.map((d, i) => ({ file: `f${String(i).padStart(4, "0")}.png`, ms: d })) };
    await fetch(`/__capture/${this.name}/meta.json`, { method: "POST", body: JSON.stringify(meta) });
  }
}
