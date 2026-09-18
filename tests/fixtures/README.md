# Test fixtures

Everything here exists so that the test suite never touches the network. CI has
no credentials and must never acquire any.

## `hapi/` — genuine, unmodified

NASA OMNI data served by SPDF is **public domain**, so these are byte-for-byte
what the server returned.

| File | What it is | Why it is here |
| --- | --- | --- |
| `omni_info.json` | A real `/info` response, trimmed to the five parameters used | Pins the dataset's declared parameter order. Requesting parameters in any other order returns HAPI error 1411 rather than data. |
| `omni_2024-05-10.csv` | Real hourly data, 10–11 May 2024 | The Gannon storm: Dst reaches −406 nT and Kp saturates at 9.0. Real extreme values, so the schema bounds are tested against the worst case that actually happened rather than an imagined one. |
| `omni_1963-01-01_with_fills.csv` | Real data containing fill markers | F10.7 is 999.9 throughout, meaning "not measured". Proves fills become `None` instead of entering the science as a solar flux of 999.9. |
| `omni_error_1411.json` | A real error body | HAPI reports failures with **HTTP 200** and a body that is neither valid CSV nor valid JSON. Captured by deliberately requesting parameters out of order. |

Regenerate by requesting the same URLs from `https://cdaweb.gsfc.nasa.gov/hapi`.

## `spacetrack/` — real structure, substituted values

**These are not real Space-Track data, and that is deliberate.**

Space-Track.org data is covered by a US-government data-use agreement that does
not permit redistribution. Committing genuine orbital elements to a public
repository would be redistribution, so these files were derived from real API
responses by keeping everything structural and replacing everything valuable.

**Kept exactly as the API returns it**, because these are what the parsing and
casting code has to cope with:

- every field name, and the field order within a record
- the fact that *every* value is a JSON string, including numbers
  (`"MEAN_MOTION": "15.06402759"`, `"NORAD_CAT_ID": "44713"`)
- `null` for absent values such as `DECAY_DATE`
- the fixed-column TLE line layout
- the decimal precision of each numeric field

**Substituted:**

- NORAD IDs, remapped into the unassigned 90001+ range
- object names and international designators
- `GP_ID` and `FILE` identifiers
- the identifying columns inside the TLE lines
- orbital values, offset by a fixed amount — same format and magnitude,
  different numbers

The last record in `gp_history_sample.json` is **deliberately invalid**: a mean
motion of 148 rev/day and an eccentricity of 1.8 are both physically impossible.
It exists so the quarantine path is tested on something that must fail, rather
than only on the happy path.

Because the values are synthetic, these fixtures test **parsing, casting,
ordering and validation** — never a scientific result. No number from this
directory reaches a mart or a figure.

To work against genuine responses locally, capture them into `data/`, which is
gitignored:

```bash
starlink-drag ingest satcat
```
