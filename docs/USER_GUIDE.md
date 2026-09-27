# User Guide: simulating radio signals in a city

**Who this is for:** anyone who wants to *run* the simulation and *understand the results*, with no prior radio
or programming knowledge. If you want to change or maintain the setup itself, read the
[Developer Guide](DEVELOPER_GUIDE.md) after this one.

**Reading time:** about 25 minutes. Sections 1–3 explain the ideas, 4–5 show how to run things, 6–8 explain the
test and its results.

---

## Contents

1. [What does this project do?](#1-what-does-this-project-do)
2. [Ten ideas you need (in plain words)](#2-ten-ideas-you-need-in-plain-words)
3. [How the pieces fit together](#3-how-the-pieces-fit-together)
4. [Getting in: opening Ubuntu and the project](#4-getting-in-opening-ubuntu-and-the-project)
5. [The commands you will use](#5-the-commands-you-will-use)
6. [How the test is conducted, step by step](#6-how-the-test-is-conducted-step-by-step)
7. [Reading the results, picture by picture](#7-reading-the-results-picture-by-picture)
8. [What the results tell us](#8-what-the-results-tell-us)
9. [Singapore: a real place on the map](#9-singapore-a-real-place-on-the-map)
10. [The moving transmitter](#10-the-moving-transmitter)
11. [The 3-D globe viewer](#11-the-3-d-globe-viewer)
12. [Realistic transmitters, 10 frequencies, and how accurate it is](#12-realistic-transmitters-10-frequencies-and-how-accurate-it-is)
13. [Changing the experiment yourself](#13-changing-the-experiment-yourself)
14. [What a simulation can and cannot tell you](#14-what-a-simulation-can-and-cannot-tell-you)
15. [When something goes wrong](#15-when-something-goes-wrong)
16. [Glossary](#16-glossary)

To remove and rebuild the whole setup by hand, see [Rebuild from zero](REBUILD_FROM_ZERO.md).

---

## 1. What does this project do?

Your phone talks to a cell tower using **radio waves**. In a city, those waves don't just fly straight to you:
they bounce off walls, bend around building corners, and get weaker with distance. Whether you have good signal
on a given street depends on all of that.

This project **simulates** that on a computer. We build a 3-D model of a city, place a transmitter (think: a
small cell tower on a roof) and some receivers (think: phones at street level), and then ask:

* How much of the signal arrives at each phone?
* By which routes does it arrive (directly, bouncing off a wall, bending around a corner)?
* Which streets have usable signal, and which don't?
* How does all this change if we use a **different frequency** (3.5 GHz, today's typical 5G, versus 28 GHz,
  "millimetre-wave" 5G)?

The last question is our first experiment. The answer, in one sentence: **28 GHz carries more data but reaches
much less of the city, especially behind buildings.** The rest of this guide shows how we measured that.

We did it twice: first in a ready-made model of **Munich** that ships with the simulator (sections 6–8), then
in a model of **Raffles Place, Singapore** built from real map data, where we also let the transmitter
**drive along a street** and show everything on a **3-D globe** in the web browser (sections 9–11).

## 2. Ten ideas you need (in plain words)

**1. Radio wave / frequency.** Radio waves are like light, only invisible and with much longer waves. The
*frequency* says how many times per second the wave wiggles. 3.5 GHz = 3.5 billion times per second.
Higher frequency means a shorter wave: about **8.6 cm** at 3.5 GHz and about **1.1 cm** at 28 GHz.

**2. Band.** A range of frequencies used for one purpose. "The 3.5 GHz band" and "the 28 GHz band" are two bands
used by 5G. In this project "band" and "frequency" are used almost interchangeably.

**3. Transmitter (TX) and receiver (RX).** The TX sends; the RX listens. We have one TX on a rooftop and three
RXs at 1.5 m height (a phone in someone's hand).

**4. Ray tracing.** The simulation fires millions of imaginary "rays" from the transmitter, like a laser
pointer sweeping in every direction, and follows each one as it hits buildings. It's the same technique used for
realistic lighting in films and video games, adapted to radio. That's what "**RT**" in "Sionna RT" means.

**5. What a ray can do when it hits something:**

```
   reflection            diffraction                 line of sight (LOS)
   (bounce off a wall)   (bend around an edge)       (nothing in the way)

   TX                    TX                          TX ----------------> RX
     \    /                \
      \  /                  \______
  -----\/------  wall        |     \  (bends around the corner)
                             | bldg \
                             |       RX
```

* **Line of sight (LOS):** a straight, clear line between TX and RX.
* **Non-line of sight (NLOS):** a building blocks the straight line. The signal can only arrive by reflection
  or diffraction.
* **Reflection:** a bounce off a wall, like a mirror.
* **Diffraction:** a small amount of energy bends around a sharp edge (a roof edge or building corner). This is
  how you get *some* signal around the corner. **Diffraction gets weaker as frequency goes up**, which is the
  main reason 28 GHz struggles behind buildings.

**The receiver names (`rx_los`, `rx_nlos`, `rx_nlos_far`).** These aren't different kinds of receiver. All
three are **identical**: the same simple antenna that listens equally in all directions, at the same height
(1.5 m, a phone in someone's hand). The only difference is **where they stand**, and the names are labels
describing each spot:

| name | meaning | Munich | Singapore |
|---|---|---|---|
| `rx_los` | receiver **with** line of sight to the transmitter | ~80 m away, in view | 90 m away, in view |
| `rx_nlos` | receiver **without** line of sight (a building blocks the straight line) | ~100 m away, behind a building | 90 m away, behind a building |
| `rx_nlos_far` | receiver without line of sight, **farther** away | ~170 m away, hidden | 160 m away, hidden |

(`tx` = transmitter, `rx` = receiver, `los` = line of sight, `nlos` = non-line of sight.)

They're placed like this on purpose, to compare three typical situations:
* `rx_los` vs `rx_nlos`: about the **same distance**, blocked or not → shows **what a building does**.
* `rx_nlos` vs `rx_nlos_far`: both blocked, different distances → shows **what extra distance does**.

One catch: the names describe the positions relative to the **fixed** transmitter. When the transmitter moves
(section 10), a receiver's situation can change. For example, `rx_nlos_far` actually *has* line of sight for the
first second, because the vehicle starts right next to it. That's why the results always report the **measured**
line of sight (the `los` column in the tables, and dots on the moving-transmitter chart) instead of trusting the
names.

**6. Path.** One complete route from TX to RX: for example "straight", or "bounce off wall A, then bend around
corner B". A receiver usually gets many paths at once (this is called **multipath**).

**7. Path gain and decibels (dB).** **Path gain is the fraction of the transmitted power that actually arrives
at the receiver.** The transmitter sends out a certain amount of power. Most of it spreads out in all
directions, hits walls, gets absorbed or goes elsewhere; only a tiny share reaches the receiver. Path gain tells
you how big that share is.

That share is always extremely small. For our line-of-sight receiver in Singapore at 3.5 GHz it's about
**0.0000000126**, roughly **1 part in 80 million**. Numbers like that are awkward, so engineers write them in
**decibels (dB)**. Each −10 dB means 10 times weaker:

| path gain | share of the power that arrives |
|---|---|
| −3 dB | half |
| −10 dB | 1 in 10 |
| −20 dB | 1 in 100 |
| −30 dB | 1 in 1,000 |
| −60 dB | 1 in 1 million |
| −80 dB | 1 in 100 million |
| −100 dB | 1 in 10 billion |

Two quick rules: **−3 dB = half**, **−10 dB = a tenth**. **Closer to zero is better**: −70 dB is a much
stronger signal than −120 dB.

Examples from our results (Singapore, section 9):
* `rx_los` at 3.5 GHz: **−79 dB**, about 1 in 80 million arrives.
* `rx_los` at 28 GHz: **−97 dB**, about 1 in 5 billion arrives.
* The difference is **18 dB, so 28 GHz delivers about 63 × less power** to the same spot (every 10 dB is × 10,
  so 18 dB ≈ 10 × 6.3).
* Behind buildings (`rx_nlos`): −108 dB vs −128 dB, a 20 dB difference, so **100 × less**.

On the maps, the coloured ground shows exactly this number for every 2 m square: **yellow ≈ −60 to −85 dB**
(strong), **green ≈ −90 to −110 dB**, **blue/purple ≈ −130 to −150 dB** (very weak).

*Why "gain" if it's always a loss?* It's the convention Sionna uses (the gain of the whole path, which always
comes out negative). Many engineers flip the sign and call it **path loss** instead (e.g. "79 dB path loss").
Same thing.

**8. Received power (dBm).** "dBm" is power compared to one milliwatt. Our transmitter sends **44 dBm**
(25 watts). The received power is simply the transmit power plus the path gain:

> 44 dBm + (−79 dB) = **−35 dBm**, about 0.3 microwatts.

That's the `rx_power_dbm` column in the tables. Phones typically still work down to around −100 dBm (depending
on the system), so −35 dBm is a very strong signal.

**9. Radio map (coverage map).** Instead of just three phones, imagine a phone on every 2 m × 2 m square of the
city. Colour each square by its path gain and you get a **radio map**: a heat map of where the signal is strong.

**10. Delay spread.** Paths have different lengths, so their copies of the signal arrive at slightly different
times (billionths of a second: *nanoseconds, ns*). The **delay spread** measures how smeared out in time those
arrivals are. A large delay spread makes life harder for the receiver (echoes overlap); systems are designed with
this in mind.

## 3. How the pieces fit together

Think of it as a stack: each layer relies on the one below it.

```
  YOU  ── look at pictures and tables, and at the 3-D globe in your Windows browser
   │
   ▼
  GLOBE VIEWER     CesiumJS web page (http://localhost:5180) showing exported results on a world map
   ▲
  EXPORTER         converts results to map formats (latitude/longitude): images, paths, markers, buildings
   ▲
  RESULTS          outputs/…  pictures (.png), tables (.csv/.md), raw data (.npz), run summary (.json)
   ▲
  THE EXPERIMENT   our scripts + notebook: "load city, place TX/RX, simulate each band, measure, draw"
   ▲
  CITY MODEL       built-in (Munich) or generated from OpenStreetMap (Singapore)
   ▲
  SIONNA RT        NVIDIA's radio ray-tracing library (knows about antennas, materials, paths, radio maps)
   ▲
  MITSUBA 3 +      the ray-tracing engine underneath (originally built for realistic images) and the
  DR.JIT           compiler that turns the maths into fast code for the graphics card or the processor
   ▲
  GPU or CPU       RTX 5080 graphics card via CUDA/OptiX (fast)  — or —  the processor via LLVM (fallback)
   ▲
  UBUNTU LINUX     runs inside Windows through "WSL2" (Windows Subsystem for Linux)
   ▲
  WINDOWS 11       with the NVIDIA graphics driver
```

The same as pictures, first the layers (purple = this project, teal = simulation software it builds on):

![layers](img/architecture_layers.svg)

and then the path the data takes from the real map to your browser:

![data flow](img/architecture_dataflow.svg)

Why so many layers?

* **Why Linux inside Windows?** Sionna is made for Linux (Ubuntu 24.04 is its recommended system). WSL2 lets us
  run a real Ubuntu inside Windows without a second computer. It's kept separate from your other WSL system
  (the one used by NVIDIA AODT), so nothing interferes.
* **Why a GPU?** Tracing millions of rays is exactly what graphics cards are built for. The whole two-band
  experiment runs in about 20 seconds; most of that is drawing pictures.
* **Why is there a CPU fallback?** If the graphics card isn't available (for example after a driver update,
  see section 14), the stack automatically uses the processor instead. Slower, same physics.
* **Why "pinned versions"?** Every software version is written down in a lock file (`uv.lock`), so the setup
  can be rebuilt identically months from now. That's what "replicable" means here.

## 4. Getting in: opening Ubuntu and the project

Everything runs inside Ubuntu. Open a **PowerShell** window (or Windows Terminal) and type:

```bash
wsl -d Ubuntu-24.04
```

The prompt changes to something like `gavin@PC:~$`. You're now in Linux. Go to the project folder:

```bash
cd ~/projects/sionna-stack
```

(`~` means "my home folder", here `/home/gavin`.) To leave Ubuntu later, type `exit`.

**Seeing the files from Windows:** paste this into the File Explorer address bar:

```
\\wsl.localhost\Ubuntu-24.04\home\gavin\projects\sionna-stack
```

Or, from inside Ubuntu in the project folder, open Explorer right there with:

```bash
explorer.exe .
```

## 5. The commands you will use

Run these from inside the project folder (section 4).

### 5.1 `./setup.sh`: "Is everything installed and healthy?"

```bash
./setup.sh
```

It checks the system, makes sure the Python environment matches the lock file, checks the graphics driver, and
ends with a report like:

```
Active backend : CUDA/OptiX (GPU)
GPU / driver   : NVIDIA GeForce RTX 5080 / 591.86
```

`[ok]` lines are good. `[!!]` lines are warnings with an explanation. It's safe to run as often as you like.

### 5.2 `uv run verify.py`: "Does a tiny simulation work?"

```bash
uv run verify.py
```

A 2-second smoke test on a small street scene. It should end with `verify: OK`. `uv run` means "run this with the
project's own Python and its exact library versions".

To also check that the processor fallback works and gives the same answer as the graphics card:

```bash
uv run verify.py --all-backends
```

### 5.3 `uv run scripts/run_experiment.py …`: "Run the real experiment"

```bash
uv run scripts/run_experiment.py configs/munich_frequency.toml
```

About 20 seconds. It prints a table and saves everything into a new folder named with the date and time, for
example `outputs/munich_frequency/20260927-164221/`. Each run gets its own folder, so old results are never
overwritten.

### 5.4 `./scripts/jupyter.sh`: "Explore interactively"

```bash
./scripts/jupyter.sh
```

This starts **JupyterLab**, a notebook in your web browser that mixes explanations, code and pictures. Copy the
printed link (it looks like `http://127.0.0.1:8890/lab?token=…`) into your Windows browser. Open
`frequency_experiment.ipynb`, then choose **Run → Run All Cells**. Scroll to the end for a **3-D view you can
rotate** (left-drag = rotate, scroll = zoom, right-drag = move). When you're finished, go back to the terminal
and press **Ctrl+C** (twice if asked) to stop it.

### 5.5 `uv run scripts/export_geo.py …`: "Put the newest results on the map"

```bash
uv run scripts/export_geo.py outputs/sg_raffles_place/latest
```

Converts a run into files the globe viewer understands (only works for scenes with real map coordinates, like
Singapore; see section 9). `latest` always means "the newest run of this experiment".

### 5.6 `./scripts/viewer.sh`: "Open the 3-D globe"

```bash
./scripts/viewer.sh
```

Starts the viewer. Leave this window open and go to **http://localhost:5180** in your Windows browser. Stop it
with **Ctrl+C**. Section 11 explains the screen.

## 6. How the test is conducted, step by step

The experiment is a **controlled comparison**: we change *one* thing (the frequency) and keep *everything else*
identical, so any difference in the results must come from the frequency.

| Kept the same | Changed |
|---|---|
| The city model (Munich) | Frequency: 3.5 GHz, then 28 GHz |
| Transmitter position and power (44 dBm) | |
| Receiver positions | |
| Antennas (simple "isotropic" ones that send equally in every direction) | |
| Simulation settings (how many bounces, which effects) | |
| Colour scale of the maps | |

All of these settings live in one text file: `configs/munich_frequency.toml`. Open it with any text editor; it's
commented.

### Step 1: Load the city

We use **Munich**, a 3-D city model that ships with Sionna (about 1.5 km × 1.2 km of real Munich streets and
buildings). Every surface has a **material** (marble, concrete, brick, metal, wood). Materials matter because
they decide how much of the wave is reflected or absorbed, and that depends on frequency. Sionna uses official
material data from the ITU (the international telecom standards body, recommendation ITU-R P.2040).

### Step 2: Place the transmitter and receivers

* **TX** on a rooftop at 27 m height, sending 44 dBm.
* **rx_los**: 80 m away along an open square, clear line of sight.
* **rx_nlos**: about 100 m away, behind a building.
* **rx_nlos_far**: about 170 m away, deeper in the side streets.

These spots weren't guessed: a quick "can I see the transmitter from here?" ray test was run over the whole
area to pick one clearly visible and two clearly hidden positions. The experiment then *measures* again whether
each receiver really has line of sight, and reports it in the `los` column.

### Step 3: Set the frequency (3.5 GHz first)

This single setting changes the wavelength and, automatically, the electrical properties of every material.

### Step 4: Trace the paths to the three receivers

Sionna fires about a million rays from the transmitter and finds every route to each receiver with up to
**5 interactions** (bounces or bends). It includes line of sight, reflections, transmission through surfaces and
**diffraction**. For every path it records how strong it is, how long it takes, and where it bounced.

### Step 5: Compute the radio map

Separately, it fires **10 million rays** and records how much power lands on each 2 m × 2 m square of an
imaginary horizontal sheet at 1.5 m height, 500 m × 500 m around the transmitter. That's 62 500 squares.

### Step 6: Measure

For each receiver: number of paths, strongest path, total received power, delay spread. For the radio map: what
share of squares got usable signal (above −130 dB path gain), and the typical (median) signal level.

### Step 7: Draw pictures and save everything

Sionna's own pictures (2-D map, 3-D city with the map painted on, the ray paths), plus our comparison charts,
tables and raw data.

### Step 8: Repeat steps 3–7 at 28 GHz, then compare

Once both bands are done, the script draws side-by-side comparisons on the **same colour scale** and writes the
summary (`manifest.json`) recording exactly what was run, on which hardware, with which software versions.

```
 config file ──► load Munich ──► place TX + 3 RX ──┐
                                                   ▼
             ┌──────────── for each band (3.5 GHz, 28 GHz) ────────────┐
             │ set frequency → trace paths → radio map → measure → draw │
             └──────────────────────────────────────────────────────────┘
                                                   ▼
                      compare bands (same scale) ──► save tables + summary
```

## 7. Reading the results, picture by picture

Results are in `outputs/munich_frequency/<date-time>/`. The pictures below come from the first real run.

### 7.1 `comparison_radio_maps.png`: the headline picture

![comparison](img/comparison_radio_maps.png)

A bird's-eye view, 500 m × 500 m. **Red triangle** = transmitter; **white dots** = receivers.

* **Colour** = path gain. Yellow/green = strong, blue/purple = weak, **white = no ray arrived** (inside a
  building, or in deep shadow).
* **Left (3.5 GHz):** the open square around the transmitter is bright, and signal spreads well into the side
  streets (the green "fingers" to the west).
* **Middle (28 GHz):** same shape, but everything is darker, and the side streets fade to blue/purple.
* **Right (difference):** how many dB weaker 28 GHz is, square by square. Orange ≈ 18 dB weaker (the open
  square), purple/black ≈ 30–50 dB weaker (behind buildings).

Because all maps use **the same colour scale**, you can compare colours directly.

### 7.2 `band_*/scene_radio_map.png`: the map painted onto the 3-D city

![3.5 GHz scene](img/scene_radio_map_3p5GHz.png)

The same radio map (3.5 GHz here) drawn onto Sionna's 3-D model of Munich. The red dot is the transmitter. This
makes it easy to see *which buildings* cast radio "shadows". Compare with `img/scene_radio_map_28GHz.png`.

### 7.3 `band_*/radio_map_sionna.png`: Sionna's own 2-D map

![Sionna 2D map](img/radio_map_sionna_3p5GHz.png)

The same information as the left panel of 7.1, drawn by Sionna's built-in `show()` function. The axes are grid
cell numbers instead of metres. Useful as a cross-check that our own chart matches Sionna's.

### 7.4 `band_*/scene_paths.png`: the actual paths

![paths](img/scene_paths_3p5GHz.png)

Each line is one path from the transmitter (red dot) to a receiver (green dots). The receiver in the open square
gets **hundreds** of paths (bounces off all the surrounding façades). The hidden receivers get only a handful,
bending over roof edges or around corners.

### 7.5 `power_delay_profiles.png`: when each path arrives

![pdp](img/power_delay_profiles.png)

One panel per receiver. Each stick is one path. **Left–right** = arrival time (nanoseconds after
transmission), **height** = strength. Blue circles = 3.5 GHz, orange squares = 28 GHz.

* In **rx_los**, the first stick (at about 280 ns) is the direct path; at 28 GHz it's about 18 dB lower.
* In **rx_nlos_far**, 3.5 GHz has a strong early path (at about 645 ns) that **disappears** at 28 GHz. That one
  missing path is why the delay spread drops so much there.

### 7.6 `path_gain_cdf.png`: how good is coverage overall?

![cdf](img/path_gain_cdf.png)

Read it like this: pick a signal level on the bottom axis, go up to a curve, and read on the left what fraction of
the (reached) map is **at or below** that level. The dashed line is the −130 dB threshold. At 3.5 GHz only about
10 % of reached squares are worse than −130 dB; at 28 GHz about 40 % are. **The further right a curve sits, the
better the coverage.**

### 7.7 `metrics.md` / `metrics.csv`: the numbers

`metrics.csv` opens in Excel. The main columns:

| column | meaning |
|---|---|
| `band_ghz` | frequency |
| `rx` / `expected` / `los` | receiver name / what we intended / what was measured (True = line of sight) |
| `num_paths` | how many routes the signal found |
| `strongest_path_gain_db` | the single best path |
| `total_path_gain_db` | all paths' power added together |
| `rx_power_dbm` | what the phone would receive (44 dBm + total path gain) |
| `rms_delay_spread_ns` | how smeared out in time the arrivals are |
| `num_paths_with_diffraction` | how many paths bent around at least one edge |

`radio_map_stats.csv` has one row per band: share of the map reached by any ray, share above −130 dB, and the
median signal.

### 7.8 `manifest.json`: the run's "lab notebook"

A text file recording *exactly* what was done: the full configuration, the graphics card and driver, all software
versions, timings, and any warnings. If someone asks "how did you get this number?", the answer is in here.

## 8. What the results tell us

| receiver | 3.5 GHz | 28 GHz | 28 GHz is weaker by |
|---|---|---|---|
| rx_los (line of sight) | −81 dB | −99 dB | **18 dB** |
| rx_nlos (behind a building) | −111 dB | −144 dB | **33 dB** |
| rx_nlos_far (deeper) | −106 dB | −145 dB | **39 dB** |
| share of map above −130 dB | 32 % | 20 % | |

**Finding 1: in the open, 28 GHz loses a predictable 18 dB.** Physics predicts that, with simple antennas, the
received power falls with the square of the frequency ratio: (28 / 3.5)² = 64 times, which is 18 dB. The
simulation gives 17.5 dB. That's a good sign it behaves correctly. (Real 28 GHz systems win this back with
**antenna arrays** that focus the beam; that's a natural next experiment.)

**Finding 2: behind buildings, 28 GHz loses much more (33–39 dB).** On top of the 18 dB, the signal that bends
around corners (diffraction) is much weaker at shorter wavelengths. So streets that are "OK" at 3.5 GHz
become "no service" at 28 GHz.

**Finding 3: fewer strong echoes at 28 GHz.** At the far receiver, a strong early path vanishes at 28 GHz, and
the delay spread drops from about 119 ns to about 6 ns. At mmWave, the channel often depends on just a few
paths, which makes it fragile (one blocked path can drop the link).

**Bottom line:** 3.5 GHz covers a neighbourhood well from one rooftop. 28 GHz needs line of sight or many more
sites (or beamforming) to cover the same streets.

## 9. Singapore: a real place on the map

The Munich model is great for learning, but it has no map coordinates: nobody recorded where its (0, 0) point
is on Earth, so it can't be placed on a globe. For Singapore we built the city model ourselves from
**OpenStreetMap** (the free, community-made world map), so we know exactly where every point is.

**How the Singapore model was made** (one command, see the [rebuild manual](REBUILD_FROM_ZERO.md) §7):

1. Download the outline and height of every building in a 600 m × 600 m square around **Raffles Place**
   (latitude 1.2840° N, longitude 103.8514° E). A copy of that download is kept in the project, so the model
   can be rebuilt identically later.
2. Turn every outline into 3-D blocks ("extrude" them) as tall as the recorded height. Buildings mapped in
   pieces (a wide base with a slimmer tower, stepped tops) are built from those pieces: 154 simple outlines plus
   120 pieces, 3–285 m tall. Where the map records the material (glass, stone, metal, concrete), it's used;
   otherwise concrete is assumed.
3. Save it in the simulator's format, together with the exact map position of its centre point.

![Singapore model](img/sg_v2_oblique.png)

*The generated Raffles Place model, rendered by Sionna. Colours show the materials: blue = glass, beige = stone,
dark grey = metal, light grey = concrete.*

**The test** is the same as in Munich (section 6): same transmitter and power, three receivers (one in view, two
hidden) chosen by the same rules (now automatic), and several frequencies. Here the transmitter is a **small
cell at 12 m** in the Raffles Place plaza with a simple "ball" antenna; section 12 adds realistic antennas.

![Singapore comparison](img/sg_ref_comparison_radio_maps.png)

*Signal strength over the area for all 10 bands, same colour scale (from the reference run).*

**Results in plain words (3.5 GHz vs 28 GHz):**

| receiver | 3.5 GHz | 28 GHz | 28 GHz is weaker by |
|---|---|---|---|
| rx_los (in view, 90 m) | −81 dB | −101 dB | 20 dB |
| rx_nlos (hidden, 90 m) | −95 dB | −117 dB | 23 dB |
| rx_nlos_far (hidden, 160 m) | −105 dB | −127 dB | 22 dB |

Surprise compared with Munich: behind buildings, 28 GHz is only ~22 dB weaker instead of 33–39 dB. Why? In
Munich the signal reaches hidden streets mostly by **bending around roof edges** (diffraction), which gets much
weaker at high frequency. Between Singapore's tall towers the signal reaches hidden spots mostly by **bouncing
off the tower walls** (reflection), like light in a corridor of mirrors, and reflections don't get much weaker
at higher frequency. **The type of city matters as much as the frequency.** That's why it's worth simulating
the real place.

## 10. The moving transmitter

"Can we move the transmitter and watch what happens?" Yes. The simulator computes one **snapshot** at a time
(where are all the paths *right now*?). To show movement, we move the transmitter a little, compute another
snapshot, and repeat, like the frames of a film.

**The test:** the same small cell is mounted on a vehicle (3 m high) and drives **300 m along the straight
street through Raffles Place at 10 m/s (36 km/h)**. That takes 30 seconds, and we compute **60 snapshots**
(every half second, every 5 m), for all 10 bands. The three receivers stay where they are.

At each snapshot we record the paths to each receiver, a full radio map, and the **Doppler shift**: the tiny
change in frequency caused by movement (the same effect that makes a passing ambulance siren sound higher, then
lower).

![gain over time](img/sg_mobility_path_gain.png)

**How to read this chart:** one panel per receiver; the line shows how strong the signal is as the vehicle drives
(time on the bottom). One line per band, from 700 MHz (top) to 140 GHz (bottom). Dots mark moments when the
receiver can **see** the vehicle.

* **rx_los** can see the street all the time. The signal peaks at about 7 s, when the vehicle passes closest.
* **rx_nlos** only sees the vehicle for a moment (around 11–13 s), when it crosses the end of its street. The
  signal jumps by about 30 dB and drops again. At 28 GHz there are deep drops (down to −160 dB) when only weak
  bounced paths remain.
* **rx_nlos_far** stands on the same street where the route starts: in view for the first ~2 seconds, then the
  vehicle turns up the street and the signal drops by 20–40 dB (more at higher bands).
* **Doppler:** at most **117 Hz at 3.5 GHz and 934 Hz at 28 GHz**, exactly what physics predicts for 10 m/s
  (the shift grows with frequency). This is a nice check that the simulation is right.

The animation `mobility/mobility_radio_maps.gif` in the run folder shows the whole radio map changing as the
vehicle drives, for all 10 bands. A smaller version with 700 MHz, 3.5 GHz and 28 GHz is below.

![moving transmitter](img/sg_mobility_radio_maps.gif)

In the viewer (Moving TX tab, 3.5 GHz) the same drive looks like this. Watch the paths to the three receivers
change as buildings come between them and the transmitter:

![moving transmitter in the viewer](img/viewer_moving.gif)

## 11. The 3-D globe viewer

The viewer puts everything on a real 3-D world map in your browser, so you can fly around Raffles Place and see
where the signal goes. Start it with `./scripts/viewer.sh` and open **http://localhost:5180**
(section 5.5–5.6: run → export → view).

![viewer overview](img/viewer_overview.gif)

Switching the band buttons (reference transmitter, same colour scale for all 10 frequencies):

![band switching](img/viewer_bands.gif)

(These animations are recorded from the viewer itself; to make them again after a new run, see Developer Guide
§24.)

**What you see on the map** (also explained in the viewer under "What am I looking at?"):

| on screen | meaning |
|---|---|
| red dot | the transmitter. In Moving TX mode it drives along the street and leaves a red trail. |
| white circles with labels | the three receivers (phones at 1.5 m) |
| coloured ground ("carpet") | signal strength at 1.5 m height, using the legend: yellow = strong (about −60 to −85 dB), green = medium, blue/purple = weak (−130 to −150 dB), no colour = no signal reached that spot (inside a building or deep shadow) |
| lines | the routes the signal takes from the transmitter to a receiver. **Colour = which receiver** (blue `rx_los`, orange `rx_nlos`, green `rx_nlos_far`, same as the chart). **Solid and thick = strong path; faint and thin = weak path.** A straight line is line of sight; a kinked line bounced off a wall or bent around an edge. |
| grey blocks | the buildings the simulation used. They're half see-through by default; change this with "building opacity". |

If the view is cluttered, press **Top view**: from straight above, the coloured ground reads like a coverage map.

**The panel on the right:**

| control | what it does |
|---|---|
| Simulation run | pick which exported run to show (newest first). New runs appear after you export them and reload the page. |
| **Fixed TX** / **Moving TX** | switch between the fixed small cell (section 9) and the driving vehicle (section 10) |
| Band | 3.5 GHz or 28 GHz. The colour scale stays the same, so colours are comparable. |
| Radio map | the coloured "carpet" of signal strength on the streets; switch it on/off and change its transparency |
| Ray paths per receiver | show/hide the lines from the transmitter to each receiver. Line colour = which receiver (blue / orange / green, same as the chart); solid and thick = strong path, faint and thin = weak path. |
| Total path gain vs time | (Moving TX) the chart from section 10, with a dashed line at the current moment |
| Link metrics | (Fixed TX) the numbers table for the selected band |
| 3-D coverage | (Fixed TX) coverage shells and the floating "signal at height" map with its height slider; see section 12.6 |
| Camera | **Top view** (straight down, like a map) or **3D view** |
| Layers | show/hide the simulation's buildings and change their transparency (make them see-through to look at paths in the street canyons) |
| Base map | OpenStreetMap (needs internet, no account), Natural Earth (works offline, very coarse), Cesium ion (real terrain and satellite images, needs a free Cesium account and token; see the rebuild manual §3.9) |

**In the map:** drag to rotate, scroll to zoom, right-drag to tilt. **Click** a receiver marker to see its numbers
(line of sight, number of paths, signal, delay spread for each band), or click a path line to see that path's
strength, delay and how it bounced.

**Moving TX mode:** only the reference run (`sg_raffles_place`) contains a moving-transmitter simulation; for the
macro and small-cell runs the **Moving TX** tab is greyed out and there is no timeline. In the reference run,
choose **Moving TX**, then press the **play ▶** button at the bottom-left (the round clock) or drag the
**timeline** at the bottom. The red dot drives along the street, leaving a trail; the paths and the radio map change with it,
and the chart cursor follows.

**Is it in the right place?** Yes. In Top view, the signal carpet fills exactly the streets of the OpenStreetMap
background, and the buildings sit exactly on the map's building outlines. The numbers at the bottom of the
panel record which computer/software produced the run and the known accuracy limits (map outlines are
typically accurate to a few metres; heights are approximate).

## 12. Realistic transmitters, 10 frequencies, and how accurate it is

Sections 6–11 used a deliberately simple transmitter (one "ball" antenna that sends equally in all directions)
and a simple city (every building one concrete block). That's good for understanding, but real networks look
different. This section describes the upgraded, more realistic simulation.

### 12.1 What was made more realistic

* **Buildings:** for 85 building volumes the map data records the material (among them 23 glass, 22 stone,
  12 metal), and many buildings are mapped in pieces (a wide base with a slimmer tower on top, stepped roofs). The model now uses all of that.
  Buildings without a recorded material are assumed concrete.
* **Rough walls:** real walls aren't perfect mirrors. Part of the signal hitting them is scattered in all
  directions ("diffuse scattering"). This is now simulated, with a roughness value per material.
* **Enough rays:** the simulation fires rays like a sprinkler. Too few rays give a speckled, unreliable map. A
  test ("convergence study") increased the number until the results stopped changing: **1 billion rays and up to
  10 bounces per map** (about 5 seconds each on the graphics card). With the old setting (10 million rays), the
  covered area was underestimated by up to 12 percentage points once rough walls were included.
* **Frequencies:** 10 bands from 700 MHz to 140 GHz (see the table below).
* **Transmitters with real antennas, power and channel widths**, so the results include how fast a phone could
  download (see 12.3).

### 12.2 A few more ideas (in plain words)

| term | meaning |
|---|---|
| **sector** | a base station usually has 3 antenna panels, each covering a 120° slice ("sector") around it |
| **antenna gain (dBi)** | how much a directional antenna concentrates power in its main direction, compared with a "ball" antenna. 14 dBi ≈ 25× stronger in the main direction (and weaker elsewhere) |
| **downtilt** | antennas on a roof point slightly downwards (here 10°) so the energy lands in the streets, not over the horizon |
| **beam / beamforming** | a grid of many small antennas (e.g. 8 × 8) can focus energy into a narrow "beam" and point it electronically. 5G mmWave cells sweep beams and serve each phone with its best one |
| **EIRP** | transmit power + antenna gain: the power a "ball" antenna would need for the same signal in the main direction. Typical mmWave small cell: ~56 dBm |
| **bandwidth** | how wide the radio channel is (10 MHz at 700 MHz … 400 MHz at 28 GHz). Wider = more data, but also more noise |
| **noise** | background radio noise the phone always receives; grows with bandwidth |
| **SNR** | signal-to-noise ratio in dB: how far the signal is above the noise. Below 0 dB a link basically doesn't work; above ~20 dB it's excellent |
| **data rate (Shannon bound)** | the theoretical maximum Mbit/s for a given bandwidth and SNR. Real 5G gets roughly half to three quarters of it, and less when many users share a cell |

### 12.3 The three experiments

| experiment | transmitter | bands | question |
|---|---|---|---|
| **reference** (`sg_raffles_place`) | "ball" antenna at 12 m in the plaza, 44 dBm, plus the driving vehicle | all 10: 700 MHz, 900 MHz, 1.8, 2.1, 2.6, 3.5, 7.5, 28, 39, 140 GHz | pure physics: what does frequency alone do? |
| **rooftop macro** (`sg_macro`) | 3 sectors on a 43 m roof (antennas at 47 m), 10° downtilt, standard 3GPP panels (~14 dBi), 46 dBm (40 W) per sector | 700 MHz – 7.5 GHz (the bands such sites carry) | what would a typical city base station deliver? |
| **mmWave small cell** (`sg_smallcell_mmwave`) | lamppost at 6 m, 2 panels along the street, each 8 × 8 antennas sweeping 18 beams, 30 dBm per panel (~56 dBm EIRP) | 28, 39 GHz (5G mmWave) and 140 GHz (6G candidate) | what would a street-level mmWave cell deliver? |

The bands are the ones used in Singapore (700/900/1800 MHz, 2.1, 2.6, 3.5 GHz, mmWave, 39 GHz in trials) plus two
6G candidates. I chose the transmitter values as typical for such sites; they're not a specific operator's
equipment.

### 12.4 Results

The rooftop macro in the viewer, band by band (received power in dBm, same colour scale):

![macro bands](img/viewer_macro_bands.gif)

**Rooftop macro** (share of the outdoor area within 250 m where the phone has usable signal, SNR above 0 dB):

| band | 700 MHz | 900 MHz | 1.8 GHz | 2.1 GHz | 2.6 GHz | 3.5 GHz | 7.5 GHz |
|---|---|---|---|---|---|---|---|
| covered | 100 % | 100 % | 99.9 % | 99.9 % | 99.8 % | 99.5 % | 96.4 % |
| typical signal (median) | −32 dBm | −34 dBm | −41 dBm | −42 dBm | −45 dBm | −48 dBm | −56 dBm |
| data-rate bound at `rx_los` | 262 Mbit/s | 255 | 448 | 435 | 421 | 1,822 | 2,901 |

**mmWave small cell** (one lamppost):

| band | 28 GHz | 39 GHz | 140 GHz |
|---|---|---|---|
| covered (SNR > 0 dB) | 46 % | 42 % | 15 % |
| data-rate bound at `rx_los` (in view, 90 m) | 2,649 Mbit/s | 2,208 | 1,444 |
| data-rate bound at `rx_nlos_far` (hidden, 160 m) | 485 Mbit/s | 299 | 31 |

**What this tells you:**
* **Lower bands reach everywhere, higher bands carry more data.** The macro covers the whole area at every band up
  to 3.5 GHz. But 3.5 GHz, with its 100 MHz channel, can deliver about 7× more data than 700 MHz (10 MHz channel)
  where the signal is good. That's why operators use both: low bands for coverage, mid bands for capacity.
* **7.5 GHz (6G "FR3") behaves much like 3.5 GHz, just a bit weaker:** 96 % coverage from a rooftop, with twice
  the channel width. That's why it's a leading 6G candidate.
* **mmWave needs line of sight or very close cells.** One lamppost covers under half of the nearby streets at
  28 GHz, and 140 GHz covers about a sixth. Where it does reach, it's extremely fast.
* The **frequency-sweep chart** (`band_summary.png` in each run folder) shows all of this in one picture: signal
  and SNR fall with frequency, but the data rate rises because of the wider channels, until coverage runs out.

![macro frequency sweep](img/sg_macro_band_summary.png)

### 12.5 How accurate is it?

Nothing here has been compared with real measurements yet, so the honest answer comes in three parts:

1. **The simulation itself is converged:** repeating it with 3× more rays changes the maps by 0.2–0.3 dB on
   average. The numbers aren't random noise.
2. **The uncertain assumptions were tested** by re-running with them changed (a "sensitivity study"):

   | if we assume … | effect on the signal |
   |---|---|
   | buildings without a recorded material are **glass** instead of concrete | about **+6 dB** on average below 6 GHz (thin glass lets low frequencies through buildings); small at mmWave |
   | walls are **perfectly smooth** | average unchanged; shadowed-area coverage −3 to −4 percentage points at 3.5–28 GHz |
   | walls are **twice as rough** | average unchanged; mmWave coverage up to +7 percentage points |

   So the **biggest uncertainty is what the untagged buildings are made of**. Many real glass façades have metal
   coatings that block signals, so reality probably lies between "glass" and "concrete".
3. **What's still missing:** trees, cars, people, detailed façades (windows, balconies), terrain, and other
   base stations (interference). These usually cost a few more dB in reality.

**Rule of thumb:** trust the comparisons (which band covers more, which site is better, how the signal changes
along a street) more than individual absolute numbers. Individual spots may be off by several dB. **To make the
absolute numbers trustworthy:** take 10–20 signal readings with a phone field-test app at known places near a
real site. The simulation can then be tuned (calibrated) to match them.

### 12.6 The signal in 3-D (coverage shells and the height slider)

The coloured ground shows the signal at 1.5 m only. Radio waves fill the whole space, so the simulation also
measures the signal on **31 invisible sheets stacked from 1.5 m up to 150 m** (every 5 m), which gives a 3-D grid
of signal strength over the whole area (about 480,000 small boxes of 4 m × 4 m × 5 m).

**What it looks like:** not a neat dome. Around the transmitter there's a bubble of strong signal, which
stretches far along open streets and over rooftops, is carved away in the "radio shadow" behind every tower, and
for a rooftop macro forms three tilted lobes (one per sector). At higher frequencies everything shrinks.

**Vertical cut** (`volume_slices.png` in each run folder): the signal on a vertical wall through the
transmitter, from ground to 150 m. Grey = inside a building. Reference transmitter (path gain, all 10 bands):

![vertical cut](img/sg_ref_volume_slices.png)

Read it like a side view: the yellow "bubble" around the red triangle is the strong signal, the darker wedges
behind the grey towers are radio shadows, and the whole picture gets darker (weaker) band by band.

**Coverage shells** (`volume_shells_3d.png`, and interactively in the viewer): a shell is a see-through 3-D
surface; **inside it the signal is stronger than the shell's level**. Here the −90 dB shell for every band
(same transmitter, same threshold):

![3-D shells](img/sg_ref_volume_shells_3d.png)

From 700 MHz to 3.5 GHz the −90 dB region fills most of the modelled block up to 150 m (open where it runs past
the box edge), with gaps in the shadow of the towers. At 7.5 GHz it has shrunk to a lumpy dome around the
transmitter, at 28/39 GHz to a small bubble a few tens of metres across, and at 140 GHz the signal never gets
that strong at all (no shell). Same place, same transmitter, only the frequency changes.

The same shell in the viewer, turning around the site while the band changes:

![shells in the viewer](img/viewer_shells.gif)

The realistic 3-sector macro (received power in dBm) shows the same trend at −45 dBm:

![macro 3-D shells](img/sg_macro_volume_shells_3d.png)

And the 6 m street-level mmWave small cell at −85 dBm: at 28/39 GHz the beams light up the street canyons and
climb the facades facing the lamp-post, at 140 GHz only a flat star along the nearest streets is left:

![small-cell 3-D shells](img/sg_smallcell_volume_shells_3d.png)

![small-cell shells in the viewer](img/viewer_smallcell_shells.gif)

**Choosing shell levels:** a shell is only informative when its level lies inside the range of values in the
volume. If a level is weaker than almost everything (e.g. −80 dBm for the 46 dBm macro), the "shell" only traces
the few weak pockets behind buildings. `uv run python tools/volume_stats.py outputs/<run>/latest` prints the
spread of values per band; pick levels between the 5 % and 95 % columns, put them in `[volume] shell_levels`,
then redraw and re-export in seconds (no recomputation):

```bash
uv run scripts/run_volume.py configs/sg_macro.toml outputs/sg_macro/latest --figures-only
uv run scripts/export_geo.py outputs/sg_macro/latest
```

**In the viewer** (section *3-D coverage* in the panel, Fixed TX mode):
* **shell checkboxes:** switch each level on or off (e.g. −100 / −90 / −80 dB for the reference run, −60 / −45 / −30 dBm for the macro). The outer (weaker) shell is
  the edge of usable signal, the inner ones the strong core. Where a shell reaches the edge of the computed box
  (250 m from the centre, 150 m high), it's left open: the signal continues beyond the modelled area.
* **signal at height:** shows the coloured map floating at the height you choose with the slider (1.5–150 m):
  e.g. "is there signal on the 20th floor?" (≈ 60 m) or "at drone height?".
* **band buttons:** switch frequency and watch the 3-D shape grow or shrink.
* Tip: use **3D view**, lower the **building opacity**, and switch the ground radio map off for a clear view.

The height slider moved from 1.5 m to 150 m (3.5 GHz). Near the ground there's a bright spot around the
transmitter and sharp radio shadows behind the towers; higher up, above most roofs, the signal is much more even
across the whole area. The purple patches that remain up there are inside the tall towers:

![signal at height](img/viewer_height.gif)

Inside buildings the shells use the simulated signal that passes through walls: at low frequencies shells go
through buildings (signal really penetrates), at mmWave they wrap around them.

## 13. Changing the experiment yourself

All changes go into the text file `configs/munich_frequency.toml`. Tip: copy it first, so the original stays
intact:

```bash
cp configs/munich_frequency.toml configs/my_test.toml
```

Edit `configs/my_test.toml` (for example in Windows Notepad via the `\\wsl.localhost\…` path, or with
`nano configs/my_test.toml` inside Ubuntu), then run it:

```bash
uv run scripts/run_experiment.py configs/my_test.toml
```

**Also give it its own name** in the file (`name = "my_test"` under `[experiment]`), so the results go to their
own folder.

Things to try:

* **Different bands:** change `ghz = [3.5, 28.0]` to e.g. `ghz = [3.5, 6.0, 28.0]`.
* **Move a receiver:** change a `position = [x, y, height]`. Numbers are metres from the centre of the map;
  the map spans −250 to +250 in x and y.
* **Stronger/weaker transmitter:** change `power_dbm` (every +3 dBm doubles the power).
* **Another built-in city:** set `source = "builtin:etoile"` (Paris, Arc de Triomphe) or
  `"builtin:san_francisco"`, then adjust the positions and camera to fit that city.
* **700 MHz (a low band):** this needs a special switch, because the official material data only starts at
  1 GHz. The results are then estimates, and the run is marked with a warning:

```bash
uv run scripts/run_experiment.py configs/munich_frequency.toml --bands 0.7 3.5 28 --extrapolate-materials
```

* **Singapore:** edit `configs/sg_raffles_place.toml` the same way. The `[mobility]` section sets the route
  (`waypoints`: start and end points in metres east/north of the centre, plus height), the speed and the number
  of snapshots. To check a new route stays on the street, and to get receiver suggestions for a new transmitter
  spot:

```bash
uv run scripts/suggest_layout.py scenes/sg_raffles_place/scene.xml --tx 20 0 12 --route -23 -148.6 3 3.1 150.3 3
```

* **Another place in the world:** see the [rebuild manual](REBUILD_FROM_ZERO.md) §7.

## 14. What a simulation can and cannot tell you

* **Simplified city.** Buildings are smooth blocks: no windows, trees, cars, people or street furniture. Real
  signals would see more scattering and blocking.
* **Material data has limits.** Materials use standard average values, and only within their official
  frequency ranges.
* **Rays are sampled.** The radio map is built from random rays, which is why far-away streets show speckled
  dots instead of smooth colour. More rays = smoother (but slower).
* **Antennas are idealised.** Isotropic antennas make comparisons clean; real base stations use directional
  antennas and beams.
* **Timings aren't benchmarks.** Speeds measured inside WSL aren't representative (the Mitsuba developers say
  so explicitly). Use them only as a rough guide.
* **Built-in cities have no map coordinates.** They can't be placed on a real globe; that's why the Singapore
  model is built from real map data.
* **Generated city models are simplified:** flat roofs, no terrain, one material (concrete) for all buildings,
  and heights taken from map tags (some estimated). Real glass towers would reflect differently at 28 GHz.
* **Movement is a series of snapshots**, every 5 m here. Anything that happens between two snapshots (a very
  short blockage) isn't seen; more snapshots give a smoother picture.

Use the simulation for **comparisons and understanding** ("which band covers more?", "which street is in
shadow?"), not as a guarantee of the exact signal at one spot.

## 15. When something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `The token '&&' is not a valid statement separator` | you typed a Linux command into Windows PowerShell | enter Ubuntu first: `wsl -d Ubuntu-24.04` |
| `No such file or directory` for `./setup.sh` | you're not in the project folder | `cd ~/projects/sionna-stack` |
| `Permission denied` for `./setup.sh` or `./scripts/…` | the file lost its "may run" flag (can happen after editing from Windows) | `chmod +x setup.sh scripts/*.sh` or run it as `bash setup.sh` |
| setup/verify says `LLVM (CPU)` and `CUDA/OptiX unavailable` | the graphics card path isn't working (typically after a Windows graphics driver update) | everything still works, just slower. To restore the GPU, see the [rebuild manual](REBUILD_FROM_ZERO.md) §6 |
| `Properties of ITU material '...' are not defined for this frequency` | the band is outside the material data range | use a band between 1 and 40 GHz, or add `--extrapolate-materials` |
| Jupyter link doesn't open | JupyterLab isn't running any more, or the port is taken | restart `./scripts/jupyter.sh`; copy the *new* link |
| `wsl -d Ubuntu-24.04` hangs | Ubuntu is stuck | in PowerShell: `wsl --terminate Ubuntu-24.04`, then try again. **Don't** use `wsl --shutdown`: it would also stop Docker/AODT |
| Viewer says "No exports found" | nothing converted for the map yet | `uv run scripts/export_geo.py outputs/sg_raffles_place/latest`, then reload the page |
| Viewer page doesn't open | the viewer isn't running | start `./scripts/viewer.sh` and keep that window open |
| Viewer map is dark / grey | map tiles still loading, or no internet in OpenStreetMap mode | wait a few seconds; offline, choose "Natural Earth (offline)" |
| New run not in the viewer's list | not exported yet | export it, then reload the page |
| `... is not georeferenced` when exporting | you exported a Munich (built-in) run | only real-map scenes (Singapore) can go on the globe |

## 16. Glossary

| term | meaning |
|---|---|
| **AODT** | NVIDIA Aerial Omniverse Digital Twin, another (separate) radio simulation platform on this PC |
| **band** | a range of radio frequencies used for one service |
| **CesiumJS** | free software that draws a 3-D globe in a web browser (used by the viewer) |
| **CZML** | Cesium's file format for things on the globe that can change over time (paths, the moving TX) |
| **Doppler shift** | small frequency change caused by movement; grows with speed and frequency |
| **extrude** | turn a flat building outline into a 3-D block of a given height |
| **georeferenced** | the model's coordinates are tied to real latitude/longitude |
| **OpenStreetMap (OSM)** | free, community-made world map; source of the Singapore buildings |
| **snapshot** | one complete simulation at one moment (one transmitter position) |
| **CUDA** | NVIDIA's way of running general computations on the graphics card |
| **dB / dBm** | logarithmic ratio / power relative to 1 milliwatt (see §2.7–2.8) |
| **delay spread** | how spread out in time the multipath arrivals are |
| **diffraction** | bending of a wave around an edge; weaker at higher frequency |
| **Dr.Jit** | the compiler under Mitsuba that turns simulation code into GPU or CPU programs |
| **GPU / CPU** | graphics card / main processor |
| **ITU-R P.2040** | international standard giving radio properties of building materials |
| **Jupyter / notebook** | a browser page mixing text, code and results |
| **LLVM** | the compiler used for the CPU fallback |
| **LOS / NLOS** | line of sight / blocked line of sight |
| **mmWave** | "millimetre wave", frequencies above ~24 GHz, like 28 GHz |
| **Mitsuba 3** | the ray-tracing engine Sionna is built on |
| **multipath** | receiving the same signal via several routes |
| **OptiX** | NVIDIA's GPU ray-tracing engine (what makes ray tracing fast on RTX cards) |
| **path gain** | fraction of transmitted power arriving via a path (in dB, negative) |
| **radio map** | heat map of signal strength over an area |
| **ray tracing** | simulating waves as many straight rays that bounce and bend |
| **Sionna / Sionna RT** | NVIDIA's open-source library for radio simulation / its ray-tracing part |
| **Ubuntu** | the Linux version used here (24.04) |
| **uv** | the tool that installs the exact Python library versions from the lock file |
| **WSL2** | Windows Subsystem for Linux: runs Linux inside Windows |
