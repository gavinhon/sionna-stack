# Rebuild from zero: remove everything and set it up again, by hand

This is the manual for doing everything **yourself, without Claude Code**: every command, in order, what it
does, what you should see, and how to undo it. Follow it top to bottom. Nothing here needs programming
knowledge. You only type commands and check the results.

* Where each command must be typed is marked:
  * **[Windows PowerShell]**: open "Windows PowerShell" or "Terminal" from the Start menu.
  * **[Ubuntu]**: inside the Linux system. Get there from PowerShell with `wsl -d Ubuntu-24.04`. The prompt then
    looks like `gavin@PC:~$`.
* Copy one command at a time, press Enter, and wait until the prompt comes back before the next one.
* Replace `gavin` with your own Linux username if you choose a different one.
* Time needed: about 45 minutes, mostly downloads (≈10 GB in total).

---

## Contents

0. [Before you delete anything: keep a copy of the project](#0-before-you-delete-anything-keep-a-copy-of-the-project)
1. [What this setup installed, and where](#1-what-this-setup-installed-and-where)
2. [Removing everything](#2-removing-everything)
3. [Rebuilding, step by step](#3-rebuilding-step-by-step)
4. [Check that everything works](#4-check-that-everything-works)
5. [Running the experiments and the viewer](#5-running-the-experiments-and-the-viewer)
6. [After a Windows graphics-driver update](#6-after-a-windows-graphics-driver-update)
7. [Making a new scene or using another place](#7-making-a-new-scene-or-using-another-place)
8. [If a step fails](#8-if-a-step-fails)

---

## 0. Before you delete anything: keep a copy of the project

The **project folder** (`~/projects/sionna-stack`) is the only thing you can't download again: it holds the
code, configurations, the Singapore scene (including the saved OpenStreetMap snapshot), and these documents.
Everything else (Python libraries, Node.js, the NVIDIA files, results) can be recreated by the commands below.

**Option A: git (recommended).** If the project is pushed to a private GitHub/GitLab repository, you only
need its URL later. Check what's saved:

```bash
cd ~/projects/sionna-stack && git status && git log --oneline | head -3
```

**Option B: a backup file on Windows.** **[Ubuntu]** This packs the project (without the large, re-creatable
folders) into `C:\Projects\sionna-stack-backup-<date>.tar.gz`:

```bash
cd ~/projects && tar --exclude=sionna-stack/.venv --exclude=sionna-stack/viewer/node_modules --exclude=sionna-stack/outputs --exclude=sionna-stack/viewer/public/data -czf /mnt/c/Projects/sionna-stack-backup-$(date +%Y%m%d).tar.gz sionna-stack
```

Check it exists (about 15 MB) in File Explorer under `C:\Projects\`. Keep a copy somewhere safe (cloud drive,
USB stick): a file on `C:` survives deleting Ubuntu, but not a broken PC.

Optional: also keep simulation results you care about (`outputs/`), e.g. copy the dated run folder to Windows.

## 1. What this setup installed, and where

Nothing was installed on Windows itself. No Windows system folders were changed.

| What | Where (inside Ubuntu) | Size | Re-created by |
|---|---|---|---|
| The Ubuntu-24.04 system (WSL distro) | whole distro | ~12 GB total | §3.1 |
| Linux user `gavin` and default-user setting | `/etc/wsl.conf` | tiny | §3.2 |
| System library `libatomic1` (apt) | `/usr/lib/x86_64-linux-gnu` | tiny | §3.3 |
| The project | `~/projects/sionna-stack` | ~5 MB + `.venv`/`node_modules`/`outputs` | §0 / §3.4 |
| uv (Python installer) | `~/.local/bin/uv`, `uvx`, `env` + one line in `~/.bashrc` and `~/.profile` | 50 MB | §3.5 |
| Python packages (download cache) | `~/.cache/uv` | 6.3 GB | §3.5 |
| Python environment | `~/projects/sionna-stack/.venv` | small (hard links into the cache) | §3.5 |
| NVIDIA Linux driver download (only extracted, never installed) | `~/nvidia-driver` | 2 GB (can be deleted after §3.6) | §3.6 |
| OptiX files for the GPU | `~/.local/lib/nvidia-optix-590.48.01` | 300 MB | §3.6 |
| Node.js 24.21.0 | `~/.local/opt/node-v24.21.0-linux-x64`, links in `~/.local/bin` | 200 MB | §3.7 |
| Viewer packages (CesiumJS, Vite) | `~/projects/sionna-stack/viewer/node_modules`, cache `~/.npm` | 200 + 160 MB | §3.8 |
| Compiled GPU/CPU kernels cache | `~/.drjit` | ~70 MB | automatic |
| Optional GUI environment | inside `~/.cache/uv` | | automatic |
| Claude Code preview config (Windows) | `C:\Projects\sionna-stack\.claude\launch.json` | tiny | not needed for manual use |

Not touched: the `Ubuntu-22.04` distro (NVIDIA AODT), Docker Desktop, `C:\Windows\System32\lxss\lib`.

## 2. Removing everything

Choose **one** of the two options.

### Option 1: delete the whole Ubuntu-24.04 system (cleanest)

⚠ **This permanently deletes every file inside Ubuntu-24.04**, including the project. Do §0 first. It doesn't
affect `Ubuntu-22.04` (AODT) or Docker Desktop.

**[Windows PowerShell]** First stop anything running in it:

```bash
wsl --terminate Ubuntu-24.04
```

Then delete it:

```bash
wsl --unregister Ubuntu-24.04
```

Check it's gone (the list should show `Ubuntu-22.04` and `docker-desktop`, but not `Ubuntu-24.04`):

```bash
wsl -l -v
```

Optionally delete `C:\Projects\sionna-stack\.claude` in File Explorer. Continue with §3.1.

### Option 2: keep Ubuntu, remove only this project and its tools

**[Ubuntu]** Stop the viewer/JupyterLab if running (Ctrl+C in their windows), then:

```bash
rm -rf ~/projects/sionna-stack
```

```bash
rm -rf ~/.local/lib/nvidia-optix-* ~/nvidia-driver
```

```bash
rm -rf ~/.local/opt/node-v* ~/.local/bin/node ~/.local/bin/npm ~/.local/bin/npx ~/.npm
```

```bash
rm -rf ~/.cache/uv ~/.local/bin/uv ~/.local/bin/uvx ~/.local/bin/env ~/.local/bin/env.fish ~/.drjit
```

Then remove the line `. "$HOME/.local/bin/env"` from `~/.bashrc` and `~/.profile` (open them with
`nano ~/.bashrc`; delete the line; save with Ctrl+O, Enter; exit with Ctrl+X). Optionally remove the apt package
(harmless to keep):

```bash
sudo apt remove -y libatomic1
```

Continue with §3.4.

## 3. Rebuilding, step by step

### 3.1 Install Ubuntu 24.04 in WSL

**[Windows PowerShell]**

```bash
wsl --install -d Ubuntu-24.04
```

A window opens and asks for a **new UNIX username** and **password**. Type `gavin` (or any name) and a password
you'll remember; it's needed for `sudo`. When you see the `gavin@...:~$` prompt, it's done.

If you're **not** asked for a username (commands later run as `root`), create the user yourself as in §3.2.

### 3.2 (Only if no user was created) create a user and make it the default

**[Windows PowerShell]** Create the user (it asks for a password):

```bash
wsl -d Ubuntu-24.04 -u root -- adduser gavin
```

Allow it to use sudo:

```bash
wsl -d Ubuntu-24.04 -u root -- usermod -aG sudo gavin
```

Make it the default user. Open the config file as root:

```bash
wsl -d Ubuntu-24.04 -u root -- nano /etc/wsl.conf
```

Make the file contain these lines (keep any existing `[boot]` section), then save (Ctrl+O, Enter) and exit
(Ctrl+X):

```
[user]
default=gavin
```

Restart only this distro:

```bash
wsl --terminate Ubuntu-24.04
```

Check (should print `gavin`):

```bash
wsl -d Ubuntu-24.04 -- whoami
```

### 3.3 System packages

**[Ubuntu]** (enter with `wsl -d Ubuntu-24.04`). This asks for your password:

```bash
sudo apt update && sudo apt install -y libatomic1 git curl
```

Why: `libatomic1` is needed by Dr.Jit (the engine under Sionna). Without it you get
`ImportError: libatomic.so.1`. `git` and `curl` fetch the project and tools.

### 3.4 Get the project back

**[Ubuntu]** With git (replace the URL with yours):

```bash
mkdir -p ~/projects && git clone <your-repository-url> ~/projects/sionna-stack
```

Or from the backup file of §0 (replace the date):

```bash
mkdir -p ~/projects && tar -xzf /mnt/c/Projects/sionna-stack-backup-YYYYMMDD.tar.gz -C ~/projects
```

Go into it (all following commands are typed here):

```bash
cd ~/projects/sionna-stack
```

### 3.5 Python environment (uv + locked packages)

```bash
bash setup.sh
```

The first run installs **uv** (into `~/.local/bin`) and then downloads the exact Python package versions listed
in `uv.lock` (≈6 GB: Sionna, PyTorch with CUDA libraries, Mitsuba, Dr.Jit, JupyterLab…). This takes 5–15 minutes.
At the end you'll see a report; `[!!]` warnings about OptiX and Node.js are expected at this point.

Close and reopen the Ubuntu window once (so the `uv` command is found), go back to the project
(`cd ~/projects/sionna-stack`), and continue.

### 3.6 GPU ray tracing (OptiX files from NVIDIA's Linux driver)

Why this is needed: inside WSL2 the file that should provide OptiX (NVIDIA's ray-tracing engine) is only a
placeholder, so the GPU can't ray-trace. This step downloads NVIDIA's official Linux driver package
(≈400 MB, from `us.download.nvidia.com`, under NVIDIA's driver licence), **extracts** five files from it
(nothing is installed) and puts them into `~/.local/lib/nvidia-optix-<version>`. Nothing on Windows changes.

First find the right version. Look at the first line of:

```bash
nvidia-smi | head -4
```

It shows e.g. `NVIDIA-SMI 590.57   Driver Version: 591.86`. The number after `NVIDIA-SMI` (here **590**.57) is the
"branch". Pick the newest Linux driver of that branch: open `https://download.nvidia.com/XFree86/Linux-x86_64/`
in a browser and look for folders starting with `590.` (for this PC: **590.48.01**). Then:

```bash
bash tools/optix_wsl_userlevel.sh 590.48.01
```

You should see `check sums and md5 sums are ok` and five `->` copy lines. Afterwards you can free 2 GB:

```bash
rm -rf ~/nvidia-driver
```

(Skipping §3.6 is fine: everything then runs on the processor instead, just slower.)

### 3.7 Node.js (for the globe viewer)

```bash
bash tools/install_node.sh
```

Downloads Node.js 24.21.0 from `nodejs.org`, checks its SHA-256 fingerprint (`...tar.xz: OK`) and installs it
into your home folder (no sudo). The Windows Node.js isn't used.

### 3.8 Final setup run

```bash
bash setup.sh
```

This time it also installs the viewer's packages (`npm ci`, exact versions from `viewer/package-lock.json`) and
should end with:

```
Active backend : CUDA/OptiX (GPU)
```

If it says `LLVM (CPU)`, see §6. To make scripts runnable by name (e.g. `./setup.sh`) after copying from
Windows:

```bash
chmod +x setup.sh verify.py scripts/*.sh scripts/*.py tools/*.sh
```

### 3.9 (Optional) Cesium ion token for real terrain and 3D buildings

Without a token the viewer works with OpenStreetMap map tiles or fully offline. For Cesium's world terrain,
satellite imagery and 3D buildings:

1. Create a free account at `https://ion.cesium.com` and create an access token (Access Tokens → Create token).
2. **[Ubuntu]** Create the settings file from the template:

```bash
cp viewer/.env.example viewer/.env
```

3. Open it with `nano viewer/.env`, paste the token after `VITE_CESIUM_ION_TOKEN=`, save and exit.
   **Never share or commit this file** (it's excluded from git).

## 4. Check that everything works

Each command should end as described.

| Command | Expected |
|---|---|
| `bash setup.sh` | ends with `Active backend : CUDA/OptiX (GPU)` |
| `uv run verify.py` | `verify: OK`, backend CUDA/OptiX |
| `uv run verify.py --all-backends` | both backends run; `\|delta total path gain\|=0.000 dB` |
| `uv run scripts/run_experiment.py configs/munich_frequency.toml --no-render` | table with `rx_los` total path gain about −81.1 dB (3.5 GHz) and −98.6 dB (28 GHz) |
| `uv run scripts/make_osm_scene.py --name sg_raffles_place --from-cache` | `154 outlines + 120 parts (26 outlines replaced by parts), heights 3-285 m` |

## 5. Running the experiments and the viewer

**Singapore (fixed + moving transmitter), about 1 minute:**

```bash
uv run scripts/run_experiment.py configs/sg_raffles_place.toml
```

**Convert the newest run for the globe viewer:**

```bash
uv run scripts/export_geo.py outputs/sg_raffles_place/latest
```

**Start the viewer** (keep this window open while you use it; stop with Ctrl+C):

```bash
bash scripts/viewer.sh
```

Open `http://localhost:5180` in your Windows browser. Every new run you export appears in the "Simulation
run" list after reloading the page.

**Everything for Singapore in one go** (reference sweep over 10 bands, rooftop macro, mmWave small cell, the
2 km Downtown Core network, sensitivity study; about 1 h 20 min; each run is exported for the viewer
automatically):

```bash
bash tools/run_all_singapore.sh
```

Follow its progress in a second window with:

```bash
bash tools/follow_log.sh
```

The sensitivity study needs the three scene variants. They're in the project already; to recreate them from the
saved map data:

```bash
uv run scripts/make_osm_scene.py --name sg_raffles_place_glass --osm-cache scenes/sg_raffles_place --default-material glass
```

```bash
uv run scripts/make_osm_scene.py --name sg_raffles_place_specular --osm-cache scenes/sg_raffles_place --scattering-scale 0
```

```bash
uv run scripts/make_osm_scene.py --name sg_raffles_place_rough2 --osm-cache scenes/sg_raffles_place --scattering-scale 2
```

**Munich (built-in scene, Phase 1)** and the **notebook**:

```bash
uv run scripts/run_experiment.py configs/munich_frequency.toml
```

```bash
bash scripts/jupyter.sh
```

Then open the printed `http://127.0.0.1:8890/lab?token=...` link.

Results are in `outputs/<experiment>/<date-time>/` (see the [User Guide](USER_GUIDE.md) for how to read them).

**Optional: re-make the documentation animations** (`docs/img/viewer_*.gif`) after new results. With the viewer
running, open `http://localhost:5180/?record=all` in the browser and leave the tab open for ~5 minutes (the
browser console, F12, prints "record: … done" for each animation). Then, in a second Ubuntu window:

```bash
uv run scripts/make_gifs.py
```

## 6. After a Windows graphics-driver update

Symptom: `setup.sh` or `verify.py` shows `LLVM (CPU)` and `CUDA/OptiX unavailable`, or a warning that the OptiX
files are from a different branch. Everything still works on the processor. To restore the GPU:

1. `nvidia-smi | head -4` → note the new branch (the number after `NVIDIA-SMI`, e.g. `595`).
2. Find the newest Linux driver of that branch at `https://download.nvidia.com/XFree86/Linux-x86_64/`.
3. `bash tools/optix_wsl_userlevel.sh <that version>`
4. `uv run verify.py --all-backends`: both should pass.
5. Remove the old folder: `rm -rf ~/.local/lib/nvidia-optix-<old version> ~/nvidia-driver`

## 7. Making a new scene or using another place

1. Find the latitude/longitude of the centre (e.g. right-click on the spot in openstreetmap.org → "Show
   address", or read it from the URL).
2. Create the scene (600 m square; the name is up to you):

```bash
uv run scripts/make_osm_scene.py --name my_area --lat 1.3000 --lon 103.8500 --size 600
```

   If it prints `504 Gateway Timeout` a few times, that's the public map server being busy; it retries
   automatically. Try again later if all attempts fail.
3. Pick a transmitter spot (x = metres east, y = metres north of the centre, height in metres) and let the tool
   suggest receivers and check a driving route:

```bash
uv run scripts/suggest_layout.py scenes/my_area/scene.xml --tx 0 0 12 --route -100 0 3 100 0 3
```

4. Copy `configs/sg_raffles_place.toml` to `configs/my_area.toml`, change `name`, `source`, the transmitter,
   the suggested receivers and the `[mobility]` waypoints.
5. Run, export, view (§5) with the new config.

## 8. If a step fails

* Read the last lines of the output. The README's **Troubleshooting** table (section 10) lists every error
  message seen while building this setup, with its cause and fix.
* Commands typed in the wrong place are the most common problem: a Linux command in PowerShell gives
  `The token '&&' is not a valid statement separator`.
* If `wsl -d Ubuntu-24.04` hangs: **[Windows PowerShell]** `wsl --terminate Ubuntu-24.04` and try again. Avoid
  `wsl --shutdown`, which also stops Docker Desktop and NVIDIA AODT.
* Nothing in this setup needs to be repeated partially: `setup.sh` can be run any number of times and only does
  what's missing.
