#!/usr/bin/env bash
# JupyterLab for interactive work. Port 8890 avoids Jupyter's default 8888 and NVIDIA AODT's ports
# (9000, 9001, 19120, 50051, 7233). WSL2 forwards localhost, so open the printed URL in a Windows browser.
cd "$(dirname "$(readlink -f "$0")")/.."
exec "$HOME/.local/bin/uv" run --frozen jupyter lab --no-browser --ip 127.0.0.1 --port "${JUPYTER_PORT:-8890}" \
  --ServerApp.root_dir="$PWD" notebooks/
