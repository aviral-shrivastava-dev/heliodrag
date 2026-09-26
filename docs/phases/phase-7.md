# Phase 7 — Analysis and publication

Plain-language version: [phase-7-plain.md](phase-7-plain.md).

**Status:** complete, 2026-09-27.

## What this phase is for

To answer the research question from the gold marts:

> Do Starlink hardware generations show statistically distinguishable
> orbital-decay sensitivity to the same space-weather forcing over Solar Cycle 25?

The brief names three confounders -- altitude shell, orbit-raising status and
the solar-cycle trend -- and warns that a naive correlation would be a fatal
flaw. The data adds a fourth, worse than the others: operational Starlinks
station-keep, so their thrusters cancel the drag being measured. The
acceptance criterion is reproducibility: delete `analysis/figures/`, run one
command, and every figure comes back identical.

## What was built

| Path | What it does |
| --- | --- |
| `analysis/models/data.py` | Reads the gold marts only: satellite phases, storm responses, descent sums. Pins the period |
| `analysis/models/fit_drag_response.py` | Storm sensitivity by weighted least squares, storm-cluster bootstrap, within-shell contrasts with Holm adjustment, and the descent cross-check |
| `analysis/models/robustness.py` | Re-runs the storm analysis with each window moved |
| `analysis/plots.py` | Seven figures, matplotlib, the explorer's palette |
| `analysis/make_figures.py` | The one command: every table and figure, `SHA256SUMS`, and `--check` |
| `analysis/figures/` | The outputs, now committed: 7 PNGs, 7 CSVs, checksums |
| `CITATION.cff`, `DATA_AVAILABILITY.md` | How to cite; what is and is not published |
| ADR-0015 | Pinned, committed, byte-reproducible outputs |

## Why it was built that way

### Station-keeping decides the design

Operational satellites hold their altitude, so their raw decay rate measures
their thrusters as much as the air. Two routes were agreed with the project
owner, and both are built, because their weaknesses differ:

1. **Storm response (the main result).** A storm raises thermospheric density
   within hours, faster than station-keeping responds. For each operational
   satellite and each storm, the response is its mean altitude rate over days
   0..+2 from the storm's peak, minus its own mean over days -6..-2. Per
   generation and home shell:

   `delta = a + beta * forcing + lambda * flux + error`

   with `forcing` the storm's peak -Dst per 100 nT and `flux` its F10.7 (centred
   on 150, per 100 sfu). `beta` is the sensitivity: extra m/day of altitude
   change per 100 nT, negative meaning faster fall.
2. **Descent cross-check.** Satellites that have left their home shell for
   good and lose at least 1 km in a month are no longer station-keeping. Within
   each satellite-month the rate, ap and F10.7 are detrended against time
   (Frisch-Waugh): a commanded descent's steady thrust and the slow drift go
   into the trend. The rate's remaining daily variation is regressed on ap's,
   with F10.7 alongside.

### The three named confounders, each explicit

- **Altitude shell.** Every estimate is within one 25 km shell, and
  generations are compared only within shells they share.
- **Orbit raising.** Each satellite's life is split in `data.py`. It is
  *raising* before it first reaches its home shell (the shell with most of its
  analysis-ready days), *operational* until it last leaves it, and *descending*
  after. The marts' `is_likely_manoeuvring` flag only catches fast rises, so the
  split is needed. Raising days are in neither analysis.
- **Solar-cycle trend.** Removed three ways: differencing against the same
  satellite's baseline days before; the storm's F10.7 in the model; and
  comparing generations only on storms both flew through, so they share the
  solar-cycle phase as well as the forcing.

### Storms, not satellites, are the independent draws

Every satellite in a storm saw the same forcing, so the bootstrap resamples
storms, 2,000 times. Because forcing is constant within a storm, OLS on
satellite rows equals WLS on per-storm means with satellite counts as weights,
which makes each resample cheap. Contrasts use the same resampled storms for
both generations. Across 7 contrasts, p-values are Holm-adjusted.

**Pre-specified before results were seen:** the windows, the 25 km shells, a
minimum of 15 storms per estimate, a baseline free of storm days, and the
analysed generations (the question's four plus v2-mini-opt). v0.9, sixty 2019
prototypes, is excluded. **Set after a first look:** a minimum of 1,000
satellite-storms per estimate, raised from 100. That was a sample-size decision
-- the cells it removed were "home shells" no Starlink operates in, such as
v1.0 at 325 km -- but it was made after seeing the estimates, and is recorded
as such.

### Validated on simulated data before use

`tests/unit/test_fit_drag_response.py` plants known sensitivities and checks
recovery. Over 200 simulations each, the storm intervals covered the truth
**95.5%** of the time (nominal 95%) and the descent intervals **92.5%**, a
little under, as percentile bootstraps over ~24 clusters tend to be. A real
difference is detected; no difference is not invented.

## Results

**80 of 121 storms** have a baseline free of storm days. **316,485**
satellite-storm responses enter the model.

**Storm sensitivity**, m/day per 100 nT of peak Dst, 95% storm-bootstrap
intervals:

| Shell | Generation | Area/mass proxy | Storms | beta | 95% interval |
| --- | --- | --- | --- | --- | --- |
| 350 km | v2-mini-dtc | 0.92 | 37 | -53.7 | -94.5 to -21.0 |
| 475 km | v2-mini | 1.20 | 47 | -34.3 | -55.6 to -4.5 |
| 475 km | v2-mini-opt | 1.59 | 27 | -49.2 | -64.1 to -22.2 |
| 525 km | v1.0 | 0.33 | 79 | -36.7 | -139.2 to -11.6 |
| 525 km | v1.5 | 0.28 | 69 | -35.6 | -65.7 to -8.9 |
| 525 km | v2-mini-opt | 1.59 | 20 | -32.6 | -174.8 to +263.6 |
| 550 km | v1.5 | 0.28 | 69 | -25.3 | -32.7 to -10.4 |
| 550 km | v2-mini | 1.20 | 44 | -30.4 | -43.9 to +13.3 |
| 550 km | v2-mini-opt | 1.59 | 21 | -20.0 | -167.9 to +86.9 |

**Contrasts within a shell, on shared storms.** One of seven is
distinguishable after Holm adjustment:

- **475 km, v2-mini-opt versus v2-mini:** v2-mini-opt falls faster, by 33.3
  m/day per 100 nT (interval 25.7 to 67.2) on the 27 storms both flew through;
  Holm-adjusted p = 0.007. That is the direction area per kilogram predicts
  (1.59 against 1.20).
- **Not distinguishable:** v1.0 versus v1.5 at 525 km (68 storms; their
  area/mass differs by 17%); v1.5 versus v2-mini at 550 km (44 storms); every
  contrast with v2-mini-opt at 525 and 550 km (19-21 storms, intervals hundreds
  of m/day wide).
- **v2-mini DTC** flies alone at 350 km and has no within-shell comparison.

**Robustness to the windows** (`robustness_*.csv`). The 475 km contrast keeps
its direction under every variant, +15 to +41 m/day. It stays distinguishable
with a longer response window (0..+3, p = 0.007). It is marginal with an
earlier baseline (-8..-3, 21 shared storms, p = 0.06), and not distinguishable
with a shorter one (0..+1, p = 0.38). The shorter window shrinks every
generation's sensitivity towards zero. Figure 2 shows why: day +1 carries a
positive swing that is the orbit-fit zig-zag the explorer documents, so a
two-day mean is mostly that artefact.

**Descent cross-check.** It covers v1.0, v1.5 and v2-mini only; v2-mini-opt and
v2-mini DTC have not spent 12 months in descent. Between 325 and 450 km it
agrees in direction: v2-mini, with four times the area per kilogram, responds
more strongly to ap than v1.0 and v1.5 in the same shells (e.g. 450 km: -37.0,
interval -63.1 to -11.1, against -2.9 and -9.4). Above 450 km it is unreliable:
v1.0 at 475 km comes out *positive*, +46.9, which drag cannot produce. Those
months are most likely commanded lowering whose thrust is not steady.

**The naive comparison is backwards.** Raw daily rate against daily Dst, no
controls: v1.0 -111.8, v2-mini-dtc -45.0, v1.5 -10.4, v2-mini -7.2, v2-mini-opt
-2.2 m/day per 100 nT (figure 7). It ranks the smallest area per kilogram as
the most sensitive, because it measures deliberate deorbits and the solar
cycle, not storms.

**Answer to the research question.** Partly. Within the one shell where two
generations of very different area per kilogram overlap with enough shared
storms (475 km), v2-mini-opt is distinguishably more storm-sensitive than
v2-mini, in the direction physics predicts, and this survives multiple-comparison
adjustment and two of three window changes. The Gen1 pair (v1.0, v1.5) is not
distinguishable, as expected from their near-identical area per kilogram. Most
other pairs cannot be compared with the storms available.

## How to check it works

```bash
uv run python -m analysis.make_figures          # writes analysis/figures/
uv run python -m analysis.make_figures --check  # "identical", or which files differ
```

Observed on 2026-09-27: `analysis/figures/` was deleted, the command was run
(about 20 seconds), and all 15 files -- 7 figures, 7 tables and `SHA256SUMS`
-- matched their previous SHA-256 hashes. On
every push, `tests/integration/test_make_figures.py` builds the outputs twice
from the fixture warehouse and requires identical bytes, with `--check`
catching a changed file. `test_analysis_boundary.py` fails if `src/` ever
imports `analysis/`.

## What is deliberately missing

- **A physical model of the response.** The sensitivities are empirical. There
  is no conversion to density through a drag coefficient, and no comparison
  with NRLMSIS.
- **Comparisons the data cannot support.** No pooled "generation effect"
  across shells, and no ranking of v2-mini DTC against the others.
- **Peer review.** This is an analysis, not a publication.

## What is genuinely unresolved

- **Thruster authority differs by generation.** SpaceX describes the Gen2
  argon Hall thrusters as giving more than twice the thrust of Gen1's. A storm response measured over
  two days is drag *minus* whatever the controller recovered. Comparisons
  within a family (v1.0/v1.5; v2-mini/v2-mini-opt) are fairer than across one.
  The positive intercepts (+9 to +31 m/day) suggest the controllers do
  overcorrect after storms.
- **The area-to-mass proxy is crude.** It is solar-array span squared over
  dry mass (`science.generations.area_to_mass_proxy`), not a ballistic
  coefficient. Starlinks fly knife-edge on station, so the real drag area
  depends on attitude and appears in no public catalogue. It orders the
  generations; it does not scale them.
- **v2-mini-opt has few storms.** It first launched in November 2024. The
  475 km result rests on 27 storms and will firm up or fade as more arrive.
- **The home-shell rule is simple.** A satellite that moved between two
  operational shells is operational only in the one where it spent most days.
- **Novelty is "not found", not proven** ([related work](../research/related-work.md)).
