#!/usr/bin/env python
"""Turn viewer recordings (outputs/captures/<scenario>/) into documentation GIFs (docs/img/viewer_<scenario>.gif).

    1. start the viewer:            bash scripts/viewer.sh
    2. record in the browser:       http://localhost:5180/?record=all      (or ?record=bands,height)
       wait until the browser console shows "record: <scenario> done" for the last scenario (~5 min for all)
    3. build the GIFs:              uv run scripts/make_gifs.py            (or: uv run scripts/make_gifs.py bands height)

Frames come with their display time (meta.json written by viewer/src/record.js).

Colours: a GIF frame holds at most 256 colours. Each GIF gets ONE shared palette: part of it is reserved for
colours that must stay exact (the radio-map colour scale, the red transmitter, the receiver/path colours, the
shell colour, caption text), the rest adapts to the frames. Per-frame adaptive palettes dropped the small red
transmitter dot and banded the colour scale. No dithering: flat map colours compress well and stay crisp.
GitHub shows GIFs inline in Markdown; each re-generated GIF is stored again in git history, so keep them
small (a few MB): --scale and --frames-step reduce size.
"""

import argparse
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

# must-keep colours (see scripts/export_geo.py and viewer/src/*.js)
FIXED = [
    (230, 30, 30), (255, 60, 60), (255, 90, 90),          # transmitter points / moving-TX trail
    (255, 255, 255), (0, 0, 0), (15, 18, 24), (207, 214, 223), (232, 236, 241),  # markers, captions
    (0x4F, 0xB3, 0xFF), (0xFF, 0xB1, 0x4F), (0x7E, 0xE0, 0x7E),  # receiver / path colours (RX_COLORS)
    (0xFF, 0x6B, 0x9A), (0xC4, 0x9B, 0xFF), (0xF5, 0xE6, 0x63),
    (0x35, 0xE0, 0xC8),                                     # shell colour in recordings
]


def legend_colors(run: str, n: int = 40) -> list[tuple[int, int, int]]:
    """n colours sampled along the run's radio-map colour scale (export.json legend stops)."""
    exp = ROOT / "viewer" / "public" / "data" / run / "export.json"
    if not exp.exists():
        return []
    stops = json.loads(exp.read_text())["legend"]["stops"]
    out = []
    for k in range(n):
        s = k / (n - 1)
        for (s0, c0), (s1, c1) in zip(stops, stops[1:]):
            if s0 <= s <= s1:
                t = 0 if s1 == s0 else (s - s0) / (s1 - s0)
                out.append(tuple(round(c0[i] + t * (c1[i] - c0[i])) for i in range(3)))
                break
    return out


def shared_palette(frames: list[Image.Image], fixed: list[tuple[int, int, int]]) -> Image.Image:
    """256-colour palette: `fixed` colours + the rest chosen (median cut) from a sample of the frames."""
    fixed = list(dict.fromkeys(fixed))[:128]
    sample = frames[:: max(1, len(frames) // 12)]
    w, h = 400, 225
    mosaic = Image.new("RGB", (w, h * len(sample)))
    for i, im in enumerate(sample):
        mosaic.paste(im.resize((w, h), Image.BILINEAR), (0, i * h))
    adaptive = mosaic.quantize(colors=256 - len(fixed), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = adaptive.getpalette()[: 3 * (256 - len(fixed))]
    flat = [v for c in fixed for v in c] + pal
    flat += [0] * (768 - len(flat))
    p = Image.new("P", (1, 1))
    p.putpalette(flat)
    return p


def build(cap: Path, out: Path, scale: float, step: int) -> tuple[int, int]:
    meta = json.loads((cap / "meta.json").read_text())
    entries = meta["frames"]
    frames, durations = [], []
    for i in range(0, len(entries), step):
        im = Image.open(cap / entries[i]["file"]).convert("RGB")
        if scale != 1.0:
            im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        frames.append(im)
        # skipped frames' display time goes to the kept frame, so the animation keeps its speed
        durations.append(sum(int(e["ms"]) for e in entries[i:i + step]))
    pal = shared_palette(frames, FIXED + legend_colors(meta.get("run", "")))
    q = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in frames]
    out.parent.mkdir(parents=True, exist_ok=True)
    q[0].save(out, save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=False, disposal=1)
    return len(q), out.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="*", help="scenario names (default: every folder in the captures dir)")
    ap.add_argument("--captures", default=str(ROOT / "outputs" / "captures"))
    ap.add_argument("--out-dir", default=str(ROOT / "docs" / "img"))
    ap.add_argument("--scale", type=float, default=1.0, help="resize factor (e.g. 0.8)")
    ap.add_argument("--frames-step", type=int, default=1, help="keep every n-th frame (2 = half the frames)")
    args = ap.parse_args()

    cap_root = Path(args.captures)
    names = args.scenarios or sorted(p.name for p in cap_root.iterdir() if (p / "meta.json").exists())
    if not names:
        raise SystemExit(f"no recordings in {cap_root}; record first: http://localhost:5180/?record=all")
    for name in names:
        n, size = build(cap_root / name, Path(args.out_dir) / f"viewer_{name}.gif", args.scale, args.frames_step)
        warn = "  <- large for a README, try --frames-step 2 or --scale 0.8" if size > 8e6 else ""
        print(f"viewer_{name}.gif: {n} frames, {size / 1e6:.2f} MB{warn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
