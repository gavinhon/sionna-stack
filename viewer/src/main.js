// sionna-stack CesiumJS viewer.
// Reads exports written by scripts/export_geo.py from /data (= viewer/public/data):
//   /data/index.json                       list of exported runs (newest first)
//   /data/<experiment>/<run_id>/export.json manifest for one run (bands, files, rectangles, legend, mobility)
// Nothing is computed here; the viewer only displays what the Python side produced.
import * as Cesium from "cesium";
import "cesium/Build/Cesium/Widgets/widgets.css";
import "./style.css";
import { drawChart, RX_COLORS } from "./chart.js";

const TOKEN = (import.meta.env.VITE_CESIUM_ION_TOKEN || "").trim();
const params = new URLSearchParams(location.search);
const baseMode = params.get("base") || (TOKEN ? "ion" : "osm"); // ion | osm | offline
if (TOKEN) Cesium.Ion.defaultAccessToken = TOKEN;

const $ = (id) => document.getElementById(id);
const state = {
  viewer: null, groundHeight: 0, run: null, exp: null, band: null, mode: "static",
  model: null, osmBuildings: null, rmLayers: {}, frameLayers: [], pathSources: {}, devices: null,
  mobilitySource: null, rxShow: {}, frameIndex: -1,
  shellModels: [], shellShow: {}, layerEntity: null, volumeToken: 0,
  dark: params.get("dark") === "1", // "dark view": only the signal is coloured (see applyDark)
};

// ---------------------------------------------------------------------------------------------- viewer
async function createViewer() {
  const viewer = new Cesium.Viewer("cesium", {
    baseLayer: false, baseLayerPicker: false, geocoder: false, sceneModePicker: false, homeButton: false,
    navigationHelpButton: false, fullscreenButton: false, animation: true, timeline: true,
    infoBox: true, selectionIndicator: true, shouldAnimate: false,
    // recording mode (?record=...) copies the WebGL canvas into GIF frames
    contextOptions: { webgl: { preserveDrawingBuffer: params.has("record") } },
  });
  viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#20262e");
  viewer.scene.globe.depthTestAgainstTerrain = false;

  if (baseMode === "ion" && TOKEN) {
    viewer.imageryLayers.add(Cesium.ImageryLayer.fromWorldImagery());
    viewer.terrainProvider = await Cesium.createWorldTerrainAsync();
    $("osm-buildings-row").hidden = false;
  } else if (baseMode === "offline") {
    viewer.imageryLayers.add(Cesium.ImageryLayer.fromProviderAsync(
      Cesium.TileMapServiceImageryProvider.fromUrl(Cesium.buildModuleUrl("Assets/Textures/NaturalEarthII"))));
  } else {
    viewer.imageryLayers.addImageryProvider(new Cesium.OpenStreetMapImageryProvider({ url: "https://tile.openstreetmap.org/" }));
  }
  $("base").value = baseMode === "ion" && !TOKEN ? "osm" : baseMode;
  if (!TOKEN) $("base").querySelector('option[value="ion"]').disabled = true;
  $("base").onchange = () => { params.set("base", $("base").value); location.search = params.toString(); };
  return viewer;
}

// Heights in the export are relative to the scene ground (origin height, usually 0 = ellipsoid).
// With real terrain, lift everything by the terrain height at the scene origin.
async function computeGroundHeight(exp) {
  if (!(state.viewer.terrainProvider instanceof Cesium.CesiumTerrainProvider)) return 0;
  const [c] = await Cesium.sampleTerrainMostDetailed(state.viewer.terrainProvider,
    [Cesium.Cartographic.fromDegrees(exp.origin.lon, exp.origin.lat)]);
  return c.height || 0;
}

function offsetCzml(doc, dh) {
  if (!dh) return doc;
  for (const p of doc) {
    const pl = p.polyline?.positions?.cartographicDegrees;
    if (pl) for (let i = 2; i < pl.length; i += 3) pl[i] += dh;
    const pos = p.position?.cartographicDegrees;
    if (pos) {
      const stride = p.position.epoch ? 4 : 3; // time-tagged samples: [t, lon, lat, h, ...]
      for (let i = stride - 1; i < pos.length; i += stride) pos[i] += dh;
    }
  }
  return doc;
}

async function loadCzml(url) {
  const doc = await (await fetch(url)).json();
  return Cesium.CzmlDataSource.load(offsetCzml(doc, state.groundHeight));
}

// ------------------------------------------------------------------------------------------- run loading
async function loadRun(path) {
  const v = state.viewer;
  v.dataSources.removeAll(true);
  Object.values(state.rmLayers).forEach((l) => v.imageryLayers.remove(l, true));
  state.frameLayers.forEach((l) => l && v.imageryLayers.remove(l, true));
  if (state.model) v.scene.primitives.remove(state.model);
  clearVolume();
  Object.assign(state, { rmLayers: {}, frameLayers: [], pathSources: {}, mobilitySource: null, frameIndex: -1,
    shellShow: {} });

  const base = `data/${path}/`;
  const exp = await (await fetch(base + "export.json")).json();
  state.exp = exp; state.run = path;
  state.groundHeight = await computeGroundHeight(exp);

  const origin = Cesium.Cartesian3.fromDegrees(exp.origin.lon, exp.origin.lat, exp.origin.height + state.groundHeight);
  state.model = await Cesium.Model.fromGltfAsync({
    url: base + exp.scene.glb,
    modelMatrix: Cesium.Transforms.eastNorthUpToFixedFrame(origin),
    upAxis: Cesium.Axis.Y, forwardAxis: Cesium.Axis.X, // file is (east, up, -north): only the Y-up -> Z-up step
    color: Cesium.Color.WHITE.withAlpha(Number($("bld-alpha").value)),
    colorBlendMode: Cesium.ColorBlendMode.MIX, colorBlendAmount: 0.0,
  });
  state.model.show = $("show-buildings").checked;
  v.scene.primitives.add(state.model);

  state.devices = await loadCzml(base + exp.devices_czml);
  v.dataSources.add(state.devices);
  for (const b of exp.bands) {
    const ds = await loadCzml(base + b.paths_czml);
    ds.show = false;
    v.dataSources.add(ds);
    state.pathSources[b.slug] = ds;
  }

  $("run-desc").textContent = exp.description + (exp.antenna_text ? ` Antenna: ${exp.antenna_text}.` : "");
  renderMaterials(exp);
  $("subtitle").textContent = `${exp.experiment} · run ${exp.run_id} · origin ${exp.origin.lat.toFixed(5)}, ${exp.origin.lon.toFixed(5)}`;
  $("tab-mobility").disabled = !exp.mobility;
  $("tab-mobility").title = exp.mobility ? "Play the transmitter moving along its route"
    : "No moving transmitter in this run: pick sg_raffles_place for the timeline and play button";
  $("mobility-hint").hidden = true; // the explanation is the button's tooltip (keeps the header short)
  buildLegend(exp.legend);
  buildVolumeControls(exp);
  buildBandButtons(exp.bands);
  buildRxToggles(exp.receivers);
  $("provenance").innerHTML =
    `Backend: ${exp.backend} · Sionna ${exp.versions["sionna-rt"]} · Mitsuba ${exp.versions.mitsuba}<br>` +
    (exp.warnings.length ? `<b>Warnings:</b> ${exp.warnings.join("; ")}<br>` : "") +
    `Accuracy: ${exp.accuracy.join(" ")}<br>Ground offset applied: ${state.groundHeight.toFixed(1)} m ` +
    `(${state.groundHeight ? "terrain height at origin" : "ellipsoid, no terrain"})<br>` +
    `Buildings${baseMode === "osm" ? " and map tiles" : ""}: © OpenStreetMap contributors (ODbL). ` +
    `Rendering: CesiumJS.`;

  state.origin = origin;
  setCamera(state.dark && exp.volume ? "dark" : "3d", 0);
  setBand(state.band && exp.bands.some((b) => b.slug === state.band) ? state.band : exp.bands[0].slug);
  await setMode(params.get("mode") === "mobility" && exp.mobility ? "mobility" : "static");
}

function setCamera(kind, duration = 1.0) {
  // 3D view: high and steep enough that the 150-280 m towers don't hide the streets.
  // "dark": lower and closer, so the height of the shells is visible (the angle of the docs GIFs)
  // distances are tuned for the 500 m Raffles Place maps and scaled with the run's map size (2 km: x4)
  const s = sceneScale();
  const range = s * ({ top: 750, dark: 950 }[kind] ?? 1150);
  const pitch = { top: -89.9, dark: -32 }[kind] ?? -58;
  const heading = kind === "top" ? 0 : 15;
  state.viewer.camera.flyToBoundingSphere(new Cesium.BoundingSphere(state.origin, 380 * s), {
    offset: new Cesium.HeadingPitchRange(Cesium.Math.toRadians(heading), Cesium.Math.toRadians(pitch), range), duration,
  });
}

function sceneScale() {
  // width of the run's radio map in metres / 500 m (the Raffles Place studies), never below 1
  const r = state.exp?.bands?.[0]?.radio_map?.rectangle;
  if (!r) return 1;
  const w = (r.east - r.west) * 111320 * Math.cos(Cesium.Math.toRadians((r.north + r.south) / 2));
  return Math.max(1, w / 500);
}

// ------------------------------------------------------------------------------------------------ UI parts
function buildLegend(lg) {
  $("rm-title").textContent = `Radio map (${lg.metric})`;
  const grad = lg.stops.map(([s, c]) => `rgba(${c[0]},${c[1]},${c[2]},1) ${s * 100}%`).join(",");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((s) => `<span>${Math.round(lg.vmin + s * (lg.vmax - lg.vmin))}</span>`).join("");
  $("legend").innerHTML = `<div class="bar" style="background:linear-gradient(90deg,${grad})"></div>` +
    `<div class="ticks">${ticks}</div><p class="muted small">${lg.metric}; same scale for every band. ` +
    `Transparent = no ray reached the cell.</p>`;
}

function buildBandButtons(bands) {
  $("bands").innerHTML = "";
  for (const b of bands) {
    const btn = document.createElement("button");
    btn.textContent = b.label; btn.dataset.slug = b.slug;
    btn.onclick = () => setBand(b.slug);
    $("bands").appendChild(btn);
  }
}

function buildRxToggles(receivers) {
  $("rx-toggles").innerHTML = "";
  receivers.forEach((r, k) => {
    if (!(r.name in state.rxShow)) state.rxShow[r.name] = true;
    const row = document.createElement("label");
    row.className = "row";
    row.innerHTML = `<input type="checkbox" ${state.rxShow[r.name] ? "checked" : ""}/>` +
      `<span class="swatch" style="background:${RX_COLORS[k % RX_COLORS.length]}"></span>${r.name}` +
      `<span class="muted">(${r.expect || ""})</span>`;
    row.querySelector("input").onchange = (e) => { state.rxShow[r.name] = e.target.checked; applyRxVisibility(); };
    $("rx-toggles").appendChild(row);
  });
}

function applyRxVisibility() {
  const setFor = (ds, prefix) => {
    if (!ds) return;
    for (const [rx, show] of Object.entries(state.rxShow)) {
      const e = ds.entities.getById(`${prefix}/${rx}`);
      if (e) e.show = show;
    }
  };
  Object.values(state.pathSources).forEach((ds) => setFor(ds, "paths"));
  setFor(state.mobilitySource, "mpaths");
}

const f1 = (v) => (v === null || v === undefined || Number.isNaN(v) ? "-" : Number(v).toFixed(1));
const f0 = (v) => (v === null || v === undefined || Number.isNaN(v) ? "-" : Math.round(Number(v)).toString());

function renderMetrics() {
  const b = state.exp.bands.find((x) => x.slug === state.band);
  const hasSnr = b.metrics.some((m) => m.snr_db !== null && m.snr_db !== undefined);
  const rows = b.metrics.map((m) =>
    `<tr><td>${m.rx}</td><td>${m.los ? "yes" : "no"}</td><td>${m.num_paths}</td>` +
    `<td>${f1(m.total_path_gain_db)}</td><td>${f1(m.rx_power_dbm)}</td>` +
    (hasSnr ? `<td>${f1(m.snr_db)}</td><td>${f0(m.shannon_capacity_mbps)}</td>` : `<td>${f1(m.rms_delay_spread_ns)}</td>`) +
    `</tr>`).join("");
  const head = hasSnr ? `<th>SNR dB</th><th>Mbit/s*</th>` : `<th>DS ns</th>`;
  const bw = b.bandwidth_mhz ? `, ${b.bandwidth_mhz} MHz channel` : "";
  $("metrics").innerHTML = `<table><tr><th>RX</th><th>LOS</th><th>paths</th><th>gain dB</th><th>dBm</th>${head}</tr>` +
    `${rows}</table><p class="muted small">${b.label}${bw}. gain = total path gain incl. antennas; dBm = received power` +
    (hasSnr ? "; SNR without interference; *Shannon upper bound (real 5G reaches ~50-75 %)." : "; DS = RMS delay spread.") +
    ` Click a marker or path for details.</p>`;
}

function renderMaterials(exp) {
  const mats = exp.materials || {};
  const names = { concrete: "concrete", glass: "glass", marble: "stone", metal: "metal", brick: "brick / tiles", wood: "wood" };
  $("materials").innerHTML = Object.entries(mats).map(([m, c]) =>
    `<span class="mat"><span class="swatch" style="background:rgb(${c.join(",")})"></span>${names[m] || m}</span>`).join("") ||
    `<span class="muted small">single material</span>`;
}

// ------------------------------------------------------------------------------------------- 3-D volume
function volumeBand() {
  return state.exp?.volume?.bands.find((b) => b.slug === state.band) || null;
}

function clearVolume() {
  const v = state.viewer;
  state.shellModels.forEach((m) => v.scene.primitives.remove(m));
  state.shellModels = [];
  if (state.layerEntity) { v.entities.remove(state.layerEntity); state.layerEntity = null; }
}

function buildVolumeControls(exp) {
  const vol = exp.volume;
  $("volume-section").hidden = !vol;
  syncVolumeTab();
  if (!vol) return;
  const levels = vol.shell_levels.slice().sort((a, b) => a - b);
  if (!Object.keys(state.shellShow).length) levels.forEach((l, i) => (state.shellShow[l] = i === 1 || levels.length === 1));
  const unit = vol.metric.includes("dBm") ? "dBm" : "dB";
  const lg = exp.legend;
  const col = (l) => {
    const s = Math.min(1, Math.max(0, (l - lg.vmin) / (lg.vmax - lg.vmin)));
    const stop = lg.stops.reduce((a, b) => (Math.abs(b[0] - s) < Math.abs(a[0] - s) ? b : a));
    return `rgb(${stop[1].slice(0, 3).join(",")})`;
  };
  $("shell-toggles").innerHTML = "";
  levels.forEach((l) => {
    const row = document.createElement("label");
    row.className = "row shell-row";
    row.dataset.level = String(l);
    row.innerHTML = `<input type="checkbox" ${state.shellShow[l] ? "checked" : ""}/>` +
      `<span class="swatch" style="background:${col(l)}"></span>shell: signal ≥ ${l} ${unit}` +
      ` <span class="muted small pct"></span>`;
    row.querySelector("input").onchange = (e) => { state.shellShow[l] = e.target.checked; showVolume(); };
    $("shell-toggles").appendChild(row);
  });
  const n = vol.bands[0]?.heights_m.length || 1;
  $("layer-h").max = String(n - 1);
  if (Number($("layer-h").value) > n - 1) $("layer-h").value = "0";
}

// What a shell shows depends on how much of the air is inside it (pct_inside, from the exporter): a level that
// almost all the air exceeds has no surface near the transmitters and only wraps the few weak pockets far away.
function fmtPct(p) {
  if (p > 0 && p < 0.5) return "<1";
  if (p >= 99.5 && p < 100) return ">99";
  return String(Math.round(p));
}

function shellExplanation(s, unit, band) {
  const p = s.pct_inside, L = `${s.level} ${unit}`;
  if (p === undefined) return "";
  const far = s.dist_outside_m != null ? `, typically ${s.dist_outside_m} m from the nearest transmitter` : "";
  const near = s.dist_inside_m != null ? ` (typically within ${s.dist_inside_m} m of a transmitter)` : "";
  if (p <= 0 && s.dist_inside_m != null) {
    return `${band}: practically no air reaches ${L} (less than 0.05 %): at most a tiny speck right at the ` +
      `antenna, usually too small to see.`;
  }
  if (p <= 0) return `${band}: no place reaches ${L} at this band, so there is no shell.`;
  if (p >= 100) return `${band}: all the air is stronger than ${L}, so there is no boundary to draw.`;
  if (p >= 85) {
    return `${band}: ${fmtPct(p)} % of the outdoor air is stronger than ${L}, so almost everything is INSIDE this ` +
      `shell. A shell is only the boundary surface, so around the transmitters there is nothing to see (it is all ` +
      `stronger). The surfaces you do see wrap the few weaker pockets (${fmtPct(100 - p)} % of the air)${far}, ` +
      `behind towers or at the edge of coverage. Empty space = stronger than ${L}.`;
  }
  if (p <= 15) {
    return `${band}: only ${fmtPct(p)} % of the air is this strong: the shell is a small bubble around the ` +
      `transmitters${near}. Everything outside the bubble is weaker than ${L}.`;
  }
  return `${band}: ${fmtPct(p)} % of the air is stronger than ${L}. The surface is the edge of this coverage: ` +
    `stronger on the transmitter side${near}, weaker beyond it${far}.`;
}

function updateShellHints() {
  const vb = volumeBand();
  const vol = state.exp?.volume;
  if (!vb || !vol) return;
  const unit = vol.metric.includes("dBm") ? "dBm" : "dB";
  const band = state.exp.bands.find((b) => b.slug === state.band)?.label || "";
  const lines = [];
  for (const row of $("shell-toggles").querySelectorAll(".shell-row")) {
    const s = vb.shells.find((x) => x.level === Number(row.dataset.level));
    const text = s ? shellExplanation(s, unit, band) : "";
    row.title = text;  // hover explanation
    row.querySelector(".pct").textContent = s?.pct_inside !== undefined ? `(${fmtPct(s.pct_inside)} % of the air inside)` : "";
    if (s && state.shellShow[s.level] && text) lines.push(`<b>${s.level} ${unit}</b> - ${text}`);
  }
  $("shell-hint").innerHTML = lines.join("<br>");
  $("shell-hint").hidden = !lines.length;
}

async function showVolume() {
  const token = ++state.volumeToken; // ignore results of superseded calls (fast band switching)
  clearVolume();
  updateShellHints();
  const vb = volumeBand();
  if (!vb || state.mode !== "static") return;
  const v = state.viewer, exp = state.exp;
  const origin = Cesium.Cartesian3.fromDegrees(exp.origin.lon, exp.origin.lat, exp.origin.height + state.groundHeight);
  for (const s of vb.shells) {
    if (!state.shellShow[s.level] || !s.glb) continue;  // no glb: this level has no surface at this band
    const m = await Cesium.Model.fromGltfAsync({
      url: `data/${state.run}/${s.glb}`,
      modelMatrix: Cesium.Transforms.eastNorthUpToFixedFrame(origin),
      upAxis: Cesium.Axis.Y, forwardAxis: Cesium.Axis.X, backFaceCulling: false,
    });
    if (token !== state.volumeToken) return;
    styleShell(m);
    v.scene.primitives.add(m);
    state.shellModels.push(m);
  }
  updateLayer();
}

function updateLayer() {
  const vb = volumeBand();
  const k = Number($("layer-h").value);
  if (vb) $("layer-h-val").textContent = `${vb.heights_m[k]} m`;
  const want = vb && state.mode === "static" && $("show-layer").checked;
  if (!want) {
    if (state.layerEntity) { state.viewer.entities.remove(state.layerEntity); state.layerEntity = null; }
    return;
  }
  const r = vb.rectangle;
  const h = vb.heights_m[k] + state.exp.origin.height + state.groundHeight;
  const image = `data/${state.run}/${vb.frames[k]}`;
  if (!state.layerEntity) {
    state.layerEntity = state.viewer.entities.add({
      rectangle: {
        coordinates: Cesium.Rectangle.fromDegrees(r.west, r.south, r.east, r.north),
        height: h,
        material: new Cesium.ImageMaterialProperty({ image, transparent: true }),
      },
    });
  } else {
    state.layerEntity.rectangle.height = h;
    state.layerEntity.rectangle.material = new Cesium.ImageMaterialProperty({ image, transparent: true });
  }
}

// ------------------------------------------------------------------------------------------- dark view
// Only the signal is coloured: dark grey street map, faint grey buildings (no material colours), no ground map
// and no ray paths, bright shells (and the height map). Active only in Fixed TX mode on runs with a 3-D volume;
// otherwise the normal look is kept (the checkbox remembers the choice). URL: ?dark=1.
const DARK_SHELL = Cesium.Color.fromCssColorString("#35e0c8");
const DARK_BUILDING = Cesium.Color.fromCssColorString("#c8ced6").withAlpha(0.12);

function darkActive() {
  return state.dark && state.mode === "static" && !!state.exp?.volume;
}

function styleShell(m) {
  const on = darkActive();
  m.color = on ? DARK_SHELL : Cesium.Color.WHITE;
  m.colorBlendMode = on ? Cesium.ColorBlendMode.MIX : Cesium.ColorBlendMode.HIGHLIGHT;
  m.colorBlendAmount = on ? 0.7 : 0.5;
}

function styleBuildings() {
  if (!state.model) return;
  const on = darkActive();
  state.model.color = on ? DARK_BUILDING : Cesium.Color.WHITE.withAlpha(Number($("bld-alpha").value));
  state.model.colorBlendAmount = on ? 1.0 : 0.0; // MIX 1 = material colours replaced by grey
}

function applyDark() {
  const on = darkActive();
  const base = state.viewer.imageryLayers.get(0);
  if (base) { base.brightness = on ? 0.3 : 1.0; base.saturation = on ? 0.2 : 1.0; }
  styleBuildings();
  state.shellModels.forEach(styleShell);
  if (state.mode === "static" && state.exp) showStaticLayers();
}

function setDark(on, moveCamera = true) {
  state.dark = on;
  $("dark-view").checked = on;
  if (on) params.set("dark", "1"); else params.delete("dark");
  history.replaceState(null, "", "?" + params.toString());
  applyDark();
  if (on && moveCamera && darkActive()) setCamera("dark");
}

// ------------------------------------------------------------------------------------------- band / mode
async function setBand(slug) {
  state.band = slug;
  [...$("bands").children].forEach((b) => b.classList.toggle("active", b.dataset.slug === slug));
  if (state.mode === "static") { showStaticLayers(); await showVolume(); } else await showMobility();
  renderMetrics();
}

function showStaticLayers() {
  const v = state.viewer;
  const b = state.exp.bands.find((x) => x.slug === state.band);
  if (!state.rmLayers[b.slug]) {
    const r = b.radio_map.rectangle;
    const layer = Cesium.ImageryLayer.fromProviderAsync(Cesium.SingleTileImageryProvider.fromUrl(
      `data/${state.run}/${b.radio_map.png}`, { rectangle: Cesium.Rectangle.fromDegrees(r.west, r.south, r.east, r.north) }));
    v.imageryLayers.add(layer);
    state.rmLayers[b.slug] = layer;
  }
  const dark = darkActive();
  for (const [slug, layer] of Object.entries(state.rmLayers)) {
    layer.show = $("show-rm").checked && !dark && slug === state.band;
    layer.alpha = Number($("rm-alpha").value);
  }
  for (const [slug, ds] of Object.entries(state.pathSources)) ds.show = slug === state.band && !dark;
  state.frameLayers.forEach((l) => l && (l.show = false));
  applyRxVisibility();
}

async function showMobility() {
  const v = state.viewer, m = state.exp.mobility;
  const mb = m.bands.find((x) => x.slug === state.band);
  Object.values(state.rmLayers).forEach((l) => (l.show = false));
  Object.values(state.pathSources).forEach((ds) => (ds.show = false));
  if (state.mobilitySource) v.dataSources.remove(state.mobilitySource, true);
  state.frameLayers.forEach((l) => l && v.imageryLayers.remove(l, true));
  state.frameLayers = new Array(mb.frames.length).fill(null);
  state.frameIndex = -1;

  const keepTime = v.clock.currentTime.clone();
  state.mobilitySource = await loadCzml(`data/${state.run}/${mb.czml}`);
  await v.dataSources.add(state.mobilitySource);
  v.clockTrackedDataSource = state.mobilitySource;
  const start = Cesium.JulianDate.fromIso8601(m.epoch);
  const stop = Cesium.JulianDate.addSeconds(start, m.times_s[m.times_s.length - 1] + m.dt_s, new Cesium.JulianDate());
  v.clock.startTime = start.clone(); v.clock.stopTime = stop.clone();
  v.clock.currentTime = Cesium.JulianDate.greaterThanOrEquals(keepTime, start) && Cesium.JulianDate.lessThan(keepTime, stop)
    ? keepTime : start.clone();
  v.clock.clockRange = Cesium.ClockRange.LOOP_STOP;
  v.clock.multiplier = 1;
  v.timeline.zoomTo(start, stop);
  applyRxVisibility();
  onTick();
}

function frameLayer(i) {
  const mb = state.exp.mobility.bands.find((x) => x.slug === state.band);
  if (!state.frameLayers[i]) {
    const r = mb.rectangle;
    const layer = Cesium.ImageryLayer.fromProviderAsync(Cesium.SingleTileImageryProvider.fromUrl(
      `data/${state.run}/${mb.frames[i]}`, { rectangle: Cesium.Rectangle.fromDegrees(r.west, r.south, r.east, r.north) }));
    layer.show = false;
    state.viewer.imageryLayers.add(layer);
    state.frameLayers[i] = layer;
  }
  return state.frameLayers[i];
}

function onTick() {
  if (state.mode !== "mobility" || !state.exp?.mobility) return;
  const m = state.exp.mobility;
  const t = Cesium.JulianDate.secondsDifference(state.viewer.clock.currentTime, Cesium.JulianDate.fromIso8601(m.epoch));
  const i = Math.max(0, Math.min(m.times_s.length - 1, Math.floor(t / m.dt_s + 1e-6)));
  if (i !== state.frameIndex) {
    const next = frameLayer(i);
    next.alpha = Number($("rm-alpha").value);
    next.show = $("show-rm").checked;
    if (state.frameIndex >= 0 && state.frameLayers[state.frameIndex]) state.frameLayers[state.frameIndex].show = false;
    frameLayer(Math.min(i + 1, m.times_s.length - 1)); // prefetch the next frame
    state.frameIndex = i;
  }
  const mb = m.bands.find((x) => x.slug === state.band);
  drawChart($("chart"), { times: m.times_s, series: mb.total_path_gain_db, los: mb.los },
    Math.max(0, Math.min(t, m.times_s[m.times_s.length - 1])), mb.slug.replace("p", ".").replace("GHz", " GHz"));
}

async function setMode(mode) {
  state.mode = mode;
  params.set("mode", mode); history.replaceState(null, "", "?" + params.toString());
  $("tab-static").classList.toggle("active", mode === "static");
  $("tab-mobility").classList.toggle("active", mode === "mobility");
  document.body.classList.toggle("static-mode", mode === "static");
  $("mobility-section").hidden = mode !== "mobility";
  $("metrics-section").hidden = mode !== "static";
  // the fixed TX marker would be misleading while the TX is moving
  for (const t of state.exp.transmitters) {
    const e = state.devices?.entities.getById(`tx/${t.name}`);
    if (e) e.show = mode === "static";
  }
  if (mode === "static") {
    if (state.mobilitySource) { state.viewer.dataSources.remove(state.mobilitySource, true); state.mobilitySource = null; }
    state.viewer.clock.shouldAnimate = false;
    showStaticLayers();
    showVolume();
  } else {
    clearVolume();
    await showMobility();
  }
  $("volume-section").hidden = !(state.exp?.volume && mode === "static");
  syncVolumeTab();
  applyDark();
}

// ------------------------------------------------------------------------------------------------- panel
// Left panel: header (run, mode, band, legend) + tabs Signal / 3-D / Receivers / Scene; help card; collapse.
function showPane(name) {
  document.querySelectorAll(".ptab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.dataset.pane === name));
  try { localStorage.setItem("sionna.pane", name); } catch { /* storage unavailable: fine */ }
}

function syncVolumeTab() {
  // the 3-D tab only makes sense for runs with a volume, in Fixed TX mode
  const off = $("volume-section").hidden;
  $("ptab-volume").disabled = off;
  $("ptab-volume").title = off ? (state.exp?.volume ? "3-D coverage is shown in Fixed TX mode" : "This run has no 3-D volume") : "";
  if (off && $("ptab-volume").classList.contains("active")) showPane("signal");
}

function setupPanel() {
  document.querySelectorAll(".ptab").forEach((b) => (b.onclick = () => !b.disabled && showPane(b.dataset.tab)));
  let saved = null;
  try { saved = localStorage.getItem("sionna.pane"); } catch { /* ignore */ }
  if (params.get("dark") === "1") saved = "volume";
  if (saved && document.querySelector(`.pane[data-pane="${saved}"]`)) showPane(saved);

  const help = $("help"), btn = $("help-btn");
  const setHelp = (open) => { help.hidden = !open; btn.setAttribute("aria-expanded", String(open)); };
  btn.onclick = () => setHelp(help.hidden);
  $("help-close").onclick = () => setHelp(false);

  const setPanel = (open) => {
    document.body.classList.toggle("panel-hidden", !open);
    $("panel-open").hidden = open;
    if (!open) setHelp(false);
  };
  $("collapse-btn").onclick = () => setPanel(false);
  $("panel-open").onclick = () => setPanel(true);
  document.body.classList.toggle("base-ion", baseMode === "ion" && !!TOKEN);
}

// ------------------------------------------------------------------------------------------------- start
async function main() {
  setupPanel();
  state.viewer = await createViewer();
  state.viewer.clock.onTick.addEventListener(onTick);
  window.sionnaViewer = state; // for debugging in the browser console: sionnaViewer.viewer, sionnaViewer.exp
  state.api = { loadRun, setBand, setMode, setDark, showStaticLayers, showVolume, updateLayer, onTick }; // record.js

  $("dark-view").checked = state.dark;
  $("dark-view").onchange = (e) => setDark(e.target.checked);

  $("layer-h").oninput = updateLayer;
  $("show-layer").onchange = updateLayer;
  $("view-top").onclick = () => setCamera("top");
  $("view-3d").onclick = () => setCamera("3d");

  $("tab-static").onclick = () => setMode("static");
  $("tab-mobility").onclick = () => setMode("mobility");
  $("show-rm").onchange = () => (state.mode === "static" ? showStaticLayers() : ((state.frameIndex = -1), onTick()));
  $("rm-alpha").oninput = () => {
    Object.values(state.rmLayers).forEach((l) => (l.alpha = Number($("rm-alpha").value)));
    state.frameLayers.forEach((l) => l && (l.alpha = Number($("rm-alpha").value)));
  };
  $("show-buildings").onchange = (e) => state.model && (state.model.show = e.target.checked);
  $("bld-alpha").oninput = styleBuildings;
  $("show-osm-buildings").onchange = async (e) => {
    if (!state.osmBuildings) state.osmBuildings = state.viewer.scene.primitives.add(await Cesium.createOsmBuildingsAsync());
    state.osmBuildings.show = e.target.checked;
  };

  let index;
  try {
    index = await (await fetch("data/index.json", { cache: "no-store" })).json();
  } catch {
    $("subtitle").textContent = "No exports found. Run scripts/export_geo.py first (see docs).";
    return;
  }
  if (!index.exports.length) { $("subtitle").textContent = "index.json is empty."; return; }
  for (const e of index.exports) {
    const opt = document.createElement("option");
    opt.value = e.path;
    opt.textContent = `${e.experiment} / ${e.run_id}${e.mobility ? " (moving TX)" : ""}`;
    $("run").appendChild(opt);
  }
  const wanted = params.get("run");
  $("run").value = index.exports.some((e) => e.path === wanted) ? wanted : index.exports[0].path;
  $("run").onchange = () => { params.set("run", $("run").value); history.replaceState(null, "", "?" + params); loadRun($("run").value); };
  await loadRun($("run").value);
  if (params.has("record")) {
    const { record } = await import("./record.js");
    await record(state, index, params.get("record"));
  }
}

main().catch((err) => {
  console.error(err);
  $("subtitle").textContent = `Error: ${err.message}`;
});
