"""Print percentiles of the outdoor 3-D volume values per band (helps choose [volume] shell_levels).
   uv run python tools/volume_stats.py outputs/<cfg>/latest [rx_power_dbm|path_gain]"""
import sys, glob, os
import numpy as np
run = sys.argv[1]
key = sys.argv[2] if len(sys.argv) > 2 else "rx_power_dbm"
for f in sorted(glob.glob(os.path.join(run, "band_*", "volume.npz"))):
    d = np.load(f)
    v = d[key] if key == "rx_power_dbm" else 10 * np.log10(np.maximum(d["path_gain"], 1e-30))
    ok = np.isfinite(v) & ~d["indoor"] & (v > -250)
    p = np.percentile(v[ok], [5, 25, 50, 75, 95])
    print(f"{os.path.basename(os.path.dirname(f)):>14}  p5 p25 p50 p75 p95: " + " ".join(f"{x:7.1f}" for x in p))
