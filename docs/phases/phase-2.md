# Phase 2 — Transformation

Plain-language version: [phase-2-plain.md](phase-2-plain.md).

**Status:** complete, 2026-09-19.

## What this phase is for

Turn a pile of raw element sets into a table you can ask the research question
of: one row per satellite per day, carrying the decay it showed, the hardware it
is, and the space weather it was flying through — with every confounder present
as a column so the analysis can condition on it explicitly.

## The problem that had to be solved first

Phase 1 left bronze readable only through pyiceberg. `dbt-duckdb` cannot read it:

- `iceberg_scan('<table dir>')` fails — no `version-hint.text`, and globbing for
  the newest metadata is disabled by default.
- `iceberg_scan('<metadata.json>')` fails on the manifest URIs, which pyiceberg
  writes as `file://D:/...` — two slashes, so the drive letter parses as a
  hostname.
- `allow_moved_paths=true`, the documented workaround, does not rescue it.

`warehouse.py` bridges it: ask pyiceberg for the files the **current snapshot**
references, and point a DuckDB view at exactly those. That is more correct than
a directory glob anyway — bronze is append-only, so a directory accumulates
superseded copies that a glob would silently include.

The views are a snapshot of a moving target, so `starlink-drag warehouse sync`
rebuilds them and `make build` runs it before dbt.

## What was built

### `science/` — pure, zero I/O

`orbital.py` converts between what Space-Track publishes and what the question
needs: semi-major axis from mean motion via Kepler's third law, altitude, period,
apsides, the mean-motion rate, and the conversion `da/dt = -(2a/3n)·dn/dt` that
turns "revolutions per day per day" into "kilometres per day", which is the only
form comparable across generations.

Checked against things true independently of this code: a geostationary orbit
comes out at 42,164 km, the ISS at ~420 km, the round trips close, and — where a
real Space-Track capture exists locally — the computed semi-major axis, period
and apsides match the values Space-Track derived itself to within a kilometre.
That last test is skipped in CI, because the data is not redistributable.

`generations.py` classifies hardware. It reports not just *what* a satellite is
but *how that is known*, because a label inferred from mass is weaker evidence
than one the source states, and Phase 7 must be able to tell them apart.

### The generation seed, from GCAT

Space-Track and CelesTrak carry neither a spacecraft-bus field nor mass, so
neither can answer the generation question. **Jonathan McDowell's GCAT** carries
both.

GCAT states the Gen2 buses outright (`Starlink V2M` / `V2MO` / `V2MD`). It
records every Gen1 bus as plain `Starlink`, so those are separated by launch-mass
banding — 0–240 kg, 240–280, 280+ — and marked `inferred_mass` rather than
`stated`.

The banding was done blind to launch dates and then validated against them:

| Generation | Satellites | Median launch mass | First launch | Span |
| --- | --- | --- | --- | --- |
| v0.9 | 60 | 227 kg | 2019-05-24 | 9 m |
| v1.0 | 1,678 | 260 kg | 2019-11-11 | 9 m |
| v1.5 | 2,938 | 300 kg | 2021-09-14 | 9 m |
| v2-mini | 2,760 | 730 kg | 2023-02-27 | 29 m |
| v2-mini-dtc | 663 | 960 kg | 2024-01-03 | 29 m |
| v2-mini-opt | 4,341 | 575 kg | 2024-11-25 | 29 m |

Every boundary lands on a publicly documented transition launch: the single 2019
prototype batch is exactly 60 satellites, v1.0 production is ~1,700, the first
laser-link batch is 2021-09-14, the first v2-mini 2023-02-27, the first
direct-to-cell 2024-01-03. Mass banding reproducing the known timeline, without
being told it, is the strongest validation available here.

GCAT is **CC-BY**, so unlike Space-Track the derived seed is committed and anyone
can regenerate it with `starlink-drag seed-generations`, no credentials needed.

### The models

Eleven models across three layers, all tagged (`silver` for staging and
intermediate, `gold` for marts) rather than foldered.

`int_gp__deduplicated` collapses on `(norad_id, epoch)` keeping the highest
`gp_id`. That key does double duty: append duplicates from a re-run collapse, and
a Space-Track *correction* — which carries a new `gp_id` — beats what it
corrected.

`int_decay__daily_rates` takes the **last** element set of each day, because
Space-Track publishes at irregular times and picking the last keeps the interval
near 24 hours. Rates are computed against the previous *available* day with the
true gap divided out; treating a five-day gap as one day would understate the
rate fivefold.

`fct_daily_decay` is the answer table. Its confounder controls are **columns, not
filters** — `altitude_shell_km`, `is_likely_manoeuvring`, `has_clean_interval`,
`epoch_date` — so Phase 7 can condition on them explicitly and a reviewer can see
what each one costs. Over 2024: 2,192,356 rows, of which 1,989,855 (91%) are
analysis-ready; the rest are flagged as manoeuvring, as having an unusable
interval, as physically implausible, or as an unknown generation.

`fct_storm_epoch` identifies storms as Dst ≤ −50 nT, collapses consecutive stormy
days onto their worst day, and reports per-generation decay by day-offset from the
peak. Superposing many storms is what separates the fast drag response from the
slow solar-cycle trend; one storm cannot.

## Verification

| Check | Result |
| --- | --- |
| `dbt build`, full year | **PASS=87, ERROR=0, WARN=1** — 11 models, 1 seed, 76 tests |
| `dbt docs generate` | complete lineage; every model described, no orphans |
| Data dictionary | generated from the manifest, 11 models and 5 sources |
| `ruff`, `ruff format`, `mypy --strict` | clean, 44 source files |
| `pytest` | 199 passed, no network |
| SQL macros vs `science/orbital.py` | 57 agreement tests, `rel=1e-12` |
| Generation seed | 12,440 payloads, 100% labelled |
| Bronze landed | 5,964,131 element sets over 366 partitions, 1 quarantined |

The one warning is deliberate: `int_decay__daily_rates` warns rather than errors
on two preliminary post-launch element fits that place objects near 3,100 km.
They are real source defects, excluded downstream, and surfacing them without
blocking the build is the point.

The one quarantined row over the whole year is worth naming, because it is what
the quarantine was built for: STARLINK-31604 arrived with a periapsis of
**−338.9 km** — 339 km below the surface — and an eccentricity of 0.118 against
Starlink's usual 0.0002. An impossible orbit, set aside with its reason and full
payload while the other 5,964,130 rows landed.

The orbital formulas exist twice — Python for testing, SQL for volume — so
`tests/unit/test_orbital_sql.py` reads the macro file, substitutes its own
constants, evaluates it in DuckDB and compares against the Python function.
A drift between the two fails the build rather than quietly changing results.

## What a failing test found

`altitude_rate_km_per_day` was bounded to ±50 km/day. Fourteen rows failed. All
fourteen were satellites at **137–298 km** — terminal re-entry, where losing
50–140 km in a day is exactly right. The bound was wrong, not the data.

The replacement is better than a wider bound. `assert_extreme_decay_is_low_altitude`
encodes the physics instead: a large rate is fine below 300 km and impossible
above it. A genuine re-entry passes; an impossible rate at 550 km fails. A second
singular test asserts that mean-motion rate and altitude rate always have
opposite signs, which would catch the minus sign disappearing from the
derivative — a change that would silently invert every result in the project.

## Deviations from the brief

**`int_decay__daily_rates` computes in SQL, not by calling `science/orbital.py`.**
The brief says to use those functions. They are scalar and this runs over
millions of rows; a Python UDF would be row-at-a-time. The formulas therefore
live in `transform/macros/orbital.sql` with the Python module as the tested
reference, and the two are pinned together by the agreement tests above. The
alternative — two copies free to drift — is what those tests exist to prevent.

**Two extra models.** `int_space_weather__daily` collapses hourly OMNI to daily,
which `fct_daily_decay` needs and the brief does not name. `dim_generation`
is specified; `dim_satellite` is too.

**`schemas/decay.py` is still not written.** Decay records are produced in dbt,
not in Python, so there is no Python boundary for Pandera to guard. The
equivalent guarantees are the dbt tests on `int_decay__daily_rates` and
`fct_daily_decay`. If Phase 7 reads decay rates back into Python, that is where
the contract belongs.

**New modules** outside the brief's listing: `warehouse.py` (bronze-to-DuckDB
bridge), `data_dictionary.py` (manifest renderer), `clients/gcat.py`,
`ingest/generation_map.py`.

## What the full year produced

`fct_daily_decay` covers all of 2024: **2,192,356 rows over 7,169 satellites**,
of which 1,989,855 (91%) are analysis-ready. `fct_storm_epoch` identifies
**18 storms**, the worst at **Dst −406 nT** — the Gannon storm of 10–11 May 2024,
which the pipeline found without being told it existed.

**The naive comparison is reported here only to show that it is wrong.** Mean
altitude rate in the two days after a storm peak:

| Generation | m/day | Area-to-mass proxy | Mean altitude |
| --- | --- | --- | --- |
| v1.0 | −4,247 | 0.33 | 534 km |
| v1.5 | −3,260 | 0.28 | 549 km |
| v2-mini | −2,253 | 1.20 | 504 km |
| v2-mini-dtc | −854 | 0.92 | 354 km |

Read naively this says the *smallest* area-to-mass satellites fall fastest,
which is backwards, and that the lowest-flying generation falls slowest, which is
also backwards. Both are artefacts. 63% of v1.0 has been retired, so much of that
fleet is being deliberately deorbited, and a commanded descent swamps a drag
response. This is the confounding the brief warned about, visible in the data,
and it is why Phase 7 exists rather than a `group by generation`.

## Unresolved — and one of these matters a lot

**Operational Starlinks station-keep, which masks the drag signal.** The median
altitude rate for v1.5 in Q1 2024 is **+0.1 m/day** — essentially zero, and
slightly *rising*. That is not a bug. Satellites on station hold their altitude
with ion thrusters, so what they measure is SpaceX's control loop, not the
atmosphere.

The brief's confounder list names altitude shell, orbit-raising and the
solar-cycle trend. Station-keeping is a fourth, and it is arguably the most
serious, because unlike the others it does not bias the estimate — it *erases*
it. `is_likely_manoeuvring` catches gross orbit-raising at −0.0005 rev/day²; it
does not catch continuous low-level station-keeping.

Two routes remain open, and Phase 7 has to choose: lean on **storm epochs**,
where drag briefly exceeds what the thrusters compensate, so a real signal
appears against the controlled baseline; or restrict to satellites **not** under
control — decaying, failed, or pre-orbit-raising. The mart supports both, and
this is flagged now rather than discovered at analysis time.

**The research question omits `v2-mini-opt`**, which at 4,341 satellites is the
largest generation in the catalogue. It first launched 2024-11-25, so it barely
appears in 2024 data, but any multi-year run will be dominated by it.

**The `unknown` bucket holds 425 satellites** that Space-Track lists and GCAT has
not classified — 3% of the catalogue, carried through as `unknown` rather than
dropped.

## Next

Phase 3 — orchestration. Partitioned Dagster assets over the same code paths,
with the warehouse sync as an explicit dependency of the dbt models, and asset
checks for freshness, row count and null rate.
