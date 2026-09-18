"""Central configuration: paths, layer layout, and credentials.

Credentials are read from the environment (or a local `.env`), never from code.
`.env` is gitignored; see `.env.example` for the required keys.
"""

from __future__ import annotations

import dataclasses
import functools
import os
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

DATA_ROOT = PROJECT_ROOT / "data"
BRONZE = DATA_ROOT / "bronze"   # raw vendor payloads, append-only, never edited
SILVER = DATA_ROOT / "silver"   # typed, deduplicated, conformed
GOLD = DATA_ROOT / "gold"       # analysis-ready marts
WAREHOUSE = DATA_ROOT / "warehouse.duckdb"

# Space-Track publishes these limits in its API documentation. We stay a margin
# under both because exceeding them earns a temporary ban, which would cost far
# more time than the throttling does.
SPACETRACK_MAX_PER_MINUTE = 20   # documented ceiling: 30
SPACETRACK_MAX_PER_HOUR = 250    # documented ceiling: 300

SPACETRACK_BASE = "https://www.space-track.org"

# Orbital elements needed for decay-rate work. Requesting a narrow predicate list
# rather than full records cuts the backfill from tens of GB to a few GB, which is
# the difference between fitting in a free tier and not.
GP_PREDICATES = [
    "NORAD_CAT_ID", "OBJECT_NAME", "OBJECT_ID", "EPOCH",
    "MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE",
    "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR", "MEAN_MOTION_DOT",
    "MEAN_MOTION_DDOT", "SEMIMAJOR_AXIS", "PERIAPSIS", "APOAPSIS",
    "REV_AT_EPOCH", "EPHEMERIS_TYPE",
]

# NASA OMNI low-resolution (hourly) dataset on the SPDF HAPI server.
OMNI_HAPI_BASE = "https://cdaweb.gsfc.nasa.gov/hapi"
OMNI_DATASET = "OMNI2_H0_MRG1HR"

# Parameter names verified against the live HAPI /info response. They are NOT the
# names used in OMNI documentation -- the HAPI server suffixes them with the
# cadence (1800 = half-hour midpoint of an hourly record).
#
# KP1800 is Kp x 10 as an integer (Kp=1- is stored as 7). The silver layer
# divides it; bronze keeps the server's own encoding.
OMNI_PARAMETERS = [
    "F10_INDEX1800",         # F10.7 daily solar radio flux
    "KP1800",                # Kp x 10, 3-hourly
    "AP_INDEX1800",          # ap index, 3-hourly
    "DST1800",               # Dst, hourly
    "AE1800",                # auroral electrojet
    "R1800",                 # daily sunspot number V2
    "Pressure1800",          # solar wind flow pressure, nPa
    "V1800",                 # solar wind speed, km/s
    "BZ_GSM1800",            # IMF Bz in GSM -- southward Bz drives storm coupling
]

# Sentinel values the server returns for "no data", read from the HAPI /info
# response. Bronze preserves them; silver converts them to nulls.
OMNI_FILL_VALUES = {
    "F10_INDEX1800": 999.9,
    "KP1800": 99,
    "AP_INDEX1800": 999,
    "DST1800": 99999,
    "AE1800": 9999,
    "R1800": 999,
    "Pressure1800": 99.99,
    "V1800": 9999.0,
    "BZ_GSM1800": 999.9,
}

# Starlink orbital history starts with the v0.9 batch.
HISTORY_START = "2019-05-24"


def _load_dotenv() -> None:
    """Populate os.environ from a local .env if present. No-op when absent."""
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclasses.dataclass(frozen=True)
class SpaceTrackCredentials:
    identity: str
    password: str


@functools.lru_cache(maxsize=1)
def spacetrack_credentials() -> SpaceTrackCredentials:
    """Read Space-Track credentials, failing loudly with actionable guidance."""
    _load_dotenv()
    identity = os.environ.get("SPACETRACK_IDENTITY", "")
    password = os.environ.get("SPACETRACK_PASSWORD", "")
    if not identity or not password:
        raise RuntimeError(
            "Space-Track credentials missing. Register free at "
            "https://www.space-track.org and create a .env file containing:\n"
            "  SPACETRACK_IDENTITY=your@email\n"
            "  SPACETRACK_PASSWORD=yourpassword"
        )
    return SpaceTrackCredentials(identity=identity, password=password)


def ensure_layers() -> None:
    """Create the lakehouse directory layout if it does not exist."""
    for path in (BRONZE, SILVER, GOLD):
        path.mkdir(parents=True, exist_ok=True)
