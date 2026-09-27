// Vite config following Cesium's official example (github.com/CesiumGS/cesium-vite-example):
// Cesium's static folders (Workers, Assets, Widgets, ThirdParty) are copied and served under /cesiumStatic,
// and CESIUM_BASE_URL tells CesiumJS where to find them.
// Port 5180: avoids Vite's default 5173 and NVIDIA AODT's ports (9000, 9001, 19120, 50051, 7233).
import fs from "node:fs";
import path from "node:path";
import { defineConfig } from "vite";
import { viteStaticCopy } from "vite-plugin-static-copy";

const cesiumSource = "node_modules/cesium/Build/Cesium";
const cesiumBaseUrl = "cesiumStatic";

// Serve /data/* (written by scripts/export_geo.py) straight from disk on every request.
// Vite indexes public/ files when the dev server starts; runs exported (or re-exported) while it is running
// would otherwise be answered with index.html. With this, "export, then reload the page" always works.
const DATA_DIR = path.resolve("public/data");
const TYPES = {
  ".json": "application/json", ".czml": "application/json", ".geojson": "application/geo+json",
  ".png": "image/png", ".glb": "model/gltf-binary", ".gif": "image/gif",
};
function liveDataPlugin() {
  return {
    name: "sionna-live-data",
    configureServer(server) {
      server.middlewares.use("/data", (req, res, next) => {
        const rel = decodeURIComponent((req.url || "/").split("?")[0]);
        const file = path.resolve(DATA_DIR, "." + rel);
        if (!file.startsWith(DATA_DIR + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) return next();
        res.setHeader("Content-Type", TYPES[path.extname(file)] || "application/octet-stream");
        res.setHeader("Cache-Control", "no-store");
        fs.createReadStream(file).pipe(res);
      });
    },
  };
}

// Recording mode (?record=..., see src/record.js) POSTs captured frames here; they are written to
// outputs/captures/<scenario>/ (git-ignored) and turned into docs GIFs by scripts/make_gifs.py.
// Dev server only (127.0.0.1); names are restricted to [a-z0-9_-] so nothing can be written elsewhere.
const CAPTURE_DIR = path.resolve("../outputs/captures");
function captureSinkPlugin() {
  return {
    name: "sionna-capture-sink",
    configureServer(server) {
      server.middlewares.use("/__capture", (req, res, next) => {
        const m = (req.url || "").match(/^\/([a-z0-9_-]+)\/([a-z0-9_-]+\.(?:png|json))$/);
        if (req.method !== "POST" || !m) return next();
        const dir = path.join(CAPTURE_DIR, m[1]);
        const chunks = [];
        req.on("data", (c) => chunks.push(c));
        req.on("end", () => {
          fs.mkdirSync(dir, { recursive: true });
          fs.writeFileSync(path.join(dir, m[2]), Buffer.concat(chunks));
          res.statusCode = 204;
          res.end();
        });
      });
    },
  };
}

export default defineConfig({
  define: {
    CESIUM_BASE_URL: JSON.stringify(`/${cesiumBaseUrl}`),
  },
  plugins: [
    liveDataPlugin(),
    captureSinkPlugin(),
    // vite-plugin-static-copy >= 4 always preserves the source path; stripBase: 4 removes
    // "node_modules/cesium/Build/Cesium" so files are served at /cesiumStatic/Assets/... as Cesium expects.
    // (Cesium's example targets an older plugin version without this behaviour.)
    viteStaticCopy({
      targets: ["ThirdParty", "Workers", "Assets", "Widgets"].map((dir) => ({
        src: `${cesiumSource}/${dir}`,
        dest: cesiumBaseUrl,
        rename: { stripBase: 4 },
      })),
    }),
  ],
  server: {
    host: "127.0.0.1", // WSL2 forwards localhost, so a Windows browser can open http://localhost:5180
    port: 5180,
    strictPort: true,
  },
  preview: { host: "127.0.0.1", port: 5180, strictPort: true },
});
