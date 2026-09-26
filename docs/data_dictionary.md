# Data dictionary

**Generated** — do not edit by hand. Descriptions live in the `schema.yml`
files beside each model; run `starlink-drag data-dictionary` to rebuild
this from dbt's manifest after `dbt docs generate`.

Last generated 2026-09-26 from 13 models and 5 sources.

Medallion layers are dbt **tags**, not folders: staging and intermediate
are `silver`, marts are `gold`, and bronze is the Iceberg landing zone
outside dbt entirely.

## Bronze sources

Not tables in the warehouse: DuckDB views over each Iceberg table's
current snapshot, rebuilt by `starlink-drag warehouse sync`. Bronze is
append-only, so a re-run leaves duplicates for the intermediate layer to
collapse — see [ADR-0005](adr/0005-bronze-appends-rather-than-replaces.md).

### `bronze.gp_history`

Space-Track general-perturbations element sets, one row per published element set per satellite. Partitioned by the epoch's date. Covered by a US-government data-use agreement; not redistributable.

| Column | Type | Description |
| --- | --- | --- |
| `gp_id` |  | Space-Track's own identifier for this element set. A corrected set gets a new one. |
| `norad_id` |  | NORAD catalogue number of the object. |
| `epoch` |  | Time the elements describe, UTC, no zone marker. |
| `mean_motion` |  | Revolutions per day. Rises as drag shrinks the orbit. |
| `epoch_date` |  | Partition key; the calendar date of `epoch`. |

### `bronze.ingest_audit`

One row per load: what was fetched, when, how many rows, how many quarantined. This is where ingest_timestamp lives, deliberately kept out of the data files so they stay byte-identical for identical input.

### `bronze.omni`

NASA OMNI hourly space weather, as delivered. Kp arrives multiplied by ten and fill markers have already become null at the ingestion boundary. Public domain.

| Column | Type | Description |
| --- | --- | --- |
| `observed_at` |  | Hour the measurement describes, UTC. |
| `f10_7_sfu` |  | Solar radio flux at 10.7 cm, in solar flux units. |
| `kp_x10` |  | Planetary K index times ten, so 90 means Kp 9.0. |
| `dst_nt` |  | Disturbance storm-time index, nT. Strongly negative during a storm. |
| `ap_nt` |  | Planetary A index, nT. |

### `bronze.quarantine`

Rows that failed their Pandera contract at ingestion, with the reason and the whole original payload. A non-empty quarantine is a signal, not a dustbin.

### `bronze.satcat`

Space-Track catalogue snapshot, partitioned by the date it was fetched. Each ingest is a complete picture of current state, so comparing two ingests is how a decay is detected.

| Column | Type | Description |
| --- | --- | --- |
| `norad_id` |  | NORAD catalogue number. |
| `decay_date` |  | Date the object re-entered, null while on orbit. |
| `ingest_date` |  | Partition key; the date this snapshot was taken. |

## Staging (silver) — one-to-one with bronze

### `stg_nasa__omni`

*view* · tags: silver · 4 tests

Hourly space weather. Kp is divided by ten here to restore its real 0-9 scale; OMNI transmits it as an integer multiple of ten.

| Column | Type | Description |
| --- | --- | --- |
| `observed_at` | TIMESTAMP | Not unique here, and that is correct. Staging is 1:1 with bronze, which is append-only, so re-running a partition leaves a duplicate. Uniqueness is asserted on int_omni__deduplicated. |
| `kp_index` | DOUBLE |  |
| `dst_nt` | INTEGER | Reached -406 nT in May 2024, the deepest of Solar Cycle 25. |
| `f10_7_sfu` | DOUBLE |  |

### `stg_spacetrack__gp_history`

*view* · tags: silver · 6 tests

Element sets, renamed and cast, 1:1 with bronze. Duplicates from re-run partitions are still present here by design.

| Column | Type | Description |
| --- | --- | --- |
| `gp_id` | BIGINT | Space-Track's identifier for this element set. |
| `norad_id` | BIGINT |  |
| `epoch_at` | TIMESTAMP |  |
| `mean_motion_rev_per_day` | DOUBLE | Revolutions per day; rises as drag shrinks the orbit. |
| `epoch_date` | DATE |  |

### `stg_spacetrack__satcat`

*view* · tags: silver · 3 tests

Catalogue snapshots, renamed and cast, 1:1 with bronze.

| Column | Type | Description |
| --- | --- | --- |
| `norad_id` | BIGINT |  |
| `ingest_date` | DATE |  |

## Intermediate (silver) — business logic

### `int_decay__daily_rates`

*view* · tags: silver · 5 tests

One row per satellite per day, from the last element set of that day, with the rate of change against the previous available day.

| Column | Type | Description |
| --- | --- | --- |
| `norad_id` | BIGINT |  |
| `epoch_date` | DATE |  |
| `mean_altitude_km` | DOUBLE | Warns rather than errors. This layer is closest to raw, and Space-Track occasionally publishes a preliminary post-launch fit placing an object thousands of kilometres from any orbit it flies. Those are excluded downstream by is_analysis_ready; surfacing them here without blocking the build is the point. |
| `interval_days` | DOUBLE | Actual gap to the previous element set, divided out of the rate. |

### `int_gp__deduplicated`

*view* · tags: silver · 2 tests

One element set per satellite per epoch. Collapses both append duplicates and Space-Track corrections by keeping the highest gp_id.

| Column | Type | Description |
| --- | --- | --- |
| `gp_id` | BIGINT |  |

### `int_omni__deduplicated`

*view* · tags: silver · 3 tests

One space-weather record per hour, collapsing the duplicates that append-only bronze leaves behind after a re-run.

| Column | Type | Description |
| --- | --- | --- |
| `observed_at` | TIMESTAMP |  |
| `epoch_date` | DATE |  |

### `int_satellite__generation_labeled`

*view* · tags: silver · 5 tests

The current catalogue snapshot with a hardware generation attached from the GCAT-derived seed. Left joined, so an unclassified satellite is labelled 'unknown' rather than disappearing.

| Column | Type | Description |
| --- | --- | --- |
| `norad_id` | BIGINT |  |
| `generation` | VARCHAR |  |
| `generation_confidence` | VARCHAR |  |

### `int_space_weather__daily`

*view* · tags: silver · 3 tests

Hourly OMNI collapsed to one row per day, each driver aggregated the way it is used: Dst by minimum, Kp by mean and max, F10.7 by mean.

| Column | Type | Description |
| --- | --- | --- |
| `epoch_date` | DATE |  |
| `hours_observed` | BIGINT |  |

## Marts (gold) — what everything else reads

### `dim_generation`

*table* · tags: gold · 8 tests

One row per hardware generation: the unit the research question compares. Masses and spans are medians, because each generation is a few discrete build variants and a mean would describe one that was never built.

| Column | Type | Description |
| --- | --- | --- |
| `generation` | VARCHAR |  |
| `satellites` | BIGINT |  |
| `decayed_pct` | DOUBLE |  |
| `median_span_m` | DOUBLE | 9 m for every Gen1 generation, 29 m for every Gen2 one. |
| `first_launch_date` | DATE | Orders the generations in time, and shows how strongly generation and epoch are collinear -- the confounder Phase 7 has to break. |

### `dim_satellite`

*table* · tags: gold · 13 tests

One row per catalogued Starlink satellite. Grain is the satellite, not the satellite-day: everything here is a property of the hardware or of its life as a whole.

| Column | Type | Description |
| --- | --- | --- |
| `norad_id` | BIGINT | NORAD catalogue number. The join key for every fact here. |
| `object_name` | VARCHAR | Catalogue name, e.g. STARLINK-1007. |
| `generation` | VARCHAR | Hardware generation from the GCAT-derived seed. 'unknown' means Space-Track lists the object but GCAT has not classified it. |
| `generation_confidence` | VARCHAR | 'high' where GCAT states the bus outright; 'medium' where it was inferred from launch-mass banding; 'none' where unknown. |
| `launch_mass_kg` | INTEGER | Launch mass from GCAT. Trimodal for Gen1, which is what the banding uses. |
| `span_m` | INTEGER | Deployed solar-array wingspan, NOT a drag cross-section. 9 m for Gen1, 29 m for Gen2. |
| `area_to_mass_proxy` | DOUBLE | span^2 / dry_mass. A relative comparison between generations under a fixed geometric assumption, never an absolute ballistic coefficient. |
| `launch_date` | DATE |  |
| `decay_date` | DATE | Re-entry date, null while on orbit. |
| `has_decayed` | BOOLEAN |  |
| `days_on_orbit` | BIGINT | Launch to decay, or to today if still on orbit. |

### `fct_daily_decay`

*table* · tags: gold · 18 tests

One row per satellite per day: the decay it showed and the space weather it was flying through. The table the research question is answered from.
The confounder controls are columns, not filters, so the analysis can condition on them explicitly and a reviewer can see what each one costs.

| Column | Type | Description |
| --- | --- | --- |
| `norad_id` | BIGINT |  |
| `epoch_date` | DATE |  |
| `generation` | VARCHAR |  |
| `mean_altitude_km` | DOUBLE | Circular-orbit altitude from mean motion, above the WGS-84 equatorial radius. |
| `altitude_rate_km_per_day` | DOUBLE | Rate of altitude change. NEGATIVE means decaying. Null on a satellite's first observed day, when there is nothing to difference against. The range is wide on purpose. A satellite in terminal re-entry below 200 km really does lose 50-140 km in a day, and a bound that rejected that would be rejecting the most drag-dominated data in the set. The physics is instead checked by assert_extreme_decay_is_low_altitude, which requires such rates to occur only where they are possible. |
| `altitude_shell_km` | DOUBLE | 25 km altitude bucket. Density falls with height, so this must be held. |
| `is_likely_manoeuvring` | BOOLEAN | Orbit rising faster than drag could explain, i.e. under thrust. |
| `has_clean_interval` | BOOLEAN | Gap to the previous element set is between 0.5 and 2 days. |
| `is_rate_physically_plausible` | BOOLEAN | The rate is one drag could produce at that altitude. Catches large orbit-raising steps, which is_likely_manoeuvring misses because they show as an apparent fall on alternate days. |
| `is_analysis_ready` | BOOLEAN | Real rate, sane interval, not manoeuvring, known generation. The headline comparison uses only these rows. |
| `dst_min_nt` | INTEGER | Worst Dst that day. Null where OMNI has not been landed for the date. |
| `f10_7_sfu` | DOUBLE | Daily solar radio flux. |

### `fct_space_weather_daily`

*table* · tags: gold · 6 tests

One row per day of space weather: F10.7, Kp, Ap and Dst, aggregated from hourly OMNI. The forcing as a series in its own right, so serving and analysis do not re-derive it from fct_daily_decay, where it is repeated once per satellite and missing on days nothing was observed.

| Column | Type | Description |
| --- | --- | --- |
| `epoch_date` | DATE |  |
| `f10_7_sfu` | DOUBLE | Daily 10.7 cm solar radio flux, in solar flux units. |
| `kp_max` | DOUBLE | Worst three-hour Kp of the day, 0 to 9. |
| `dst_min_nt` | INTEGER | Worst hourly Dst of the day. More negative is a stronger storm. |
| `is_storm_day` | BOOLEAN | Dst at or below -50 nT, the threshold fct_storm_epoch uses. False where Dst was not measured. |

### `fct_storm_epoch`

*table* · tags: gold · 12 tests

Superposed-epoch input: for each storm, each generation and each day offset from the storm's peak, the mean decay observed.
Superposing many storms is what separates the fast drag response from the slow solar-cycle trend. A storm is Dst at or below -50 nT; consecutive stormy days collapse into one event keyed on its worst day.

| Column | Type | Description |
| --- | --- | --- |
| `storm_id` | HUGEINT |  |
| `peak_date` | DATE |  |
| `peak_dst_nt` | INTEGER | Minimum Dst of the event. More negative is more severe. |
| `generation` | VARCHAR |  |
| `days_from_peak` | BIGINT | Offset from the storm peak; negative is before. |
| `observations` | BIGINT |  |
| `satellites` | BIGINT |  |
| `mean_altitude_rate_km_per_day` | DOUBLE | Mean over satellites of that generation in that shell on that day. |
