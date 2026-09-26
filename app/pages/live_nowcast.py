"""Live drag nowcast -- the streaming path's page (Phase 6).

A second page of the explorer, picked up by Streamlit from ``app/pages/``, so
the batch explorer's script is untouched. Layout only: reading and charting
are in ``starlink_drag.serving.live``. It reads the nowcast's own Iceberg table
and never the warehouse, and redraws itself every minute.
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

from starlink_drag.config import get_settings
from starlink_drag.serving import live
from starlink_drag.stream.table import latest, read_nowcast

st.set_page_config(page_title="Live drag nowcast", layout="wide")


def _age(delta: dt.timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"


@st.fragment(run_every=60)
def nowcast() -> None:
    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    frame = read_nowcast(settings, since=(now - live.HISTORY).date())
    if frame.is_empty():
        st.info(
            "No nowcast yet. The streaming path runs separately from the batch pipeline: "
            "`docker compose -f infra/docker/docker-compose.yml --profile streaming up -d`, "
            "or `starlink-drag stream produce` and `starlink-drag stream consume` "
            "with a Redpanda broker."
        )
        return

    current = latest(frame)
    stale_after = dt.timedelta(seconds=2 * settings.stream.snapshot_seconds + 300)
    fresh = live.freshness(current, now, stale_after=stale_after)
    if fresh.stale:
        st.warning(
            f"The last nowcast was computed {_age(fresh.snapshot_age)}. Is the consumer running?"
        )

    tiles = st.columns(6)
    row = current.row(0, named=True)
    tiles[0].metric("Kp now (estimated)", _value(row["kp_estimated"]))
    tiles[1].metric("Kp, last 3 h", _value(row["kp"]))
    tiles[2].metric("ap", _value(row["ap"]))
    tiles[3].metric("Dst (nT)", _value(row["dst"]))
    tiles[4].metric("F10.7 (sfu)", _value(row["f107"]))
    tiles[5].metric("Newest element set", _age(fresh.epoch_age))
    st.caption(
        f"Nowcast computed {_age(fresh.snapshot_age)} over the "
        f"{int(row['window_hours'])} hours of element sets up to "
        f"{fresh.newest_epoch:%Y-%m-%d %H:%M} UTC. Kp observed "
        f"{_when(row['kp_estimated_observed_at'])}, Dst {_when(row['dst_observed_at'])}, "
        f"F10.7 {_when(row['f107_observed_at'])}."
    )

    st.dataframe(
        live.generation_table(current),
        hide_index=True,
        width="stretch",
        column_config={
            "Median BSTAR (1/earth radii)": st.column_config.NumberColumn(format="%.2e")
        },
    )

    history = live.recent(frame, now)
    theme = st.context.theme
    dark = theme is not None and theme.type == "dark"
    st.subheader("Median BSTAR, last 48 hours")
    st.altair_chart(
        live.history_chart(history, "median_bstar", "BSTAR", dark=dark, number_format=".1e"),
        width="stretch",
    )
    st.subheader("Median altitude change, last 48 hours")
    if history["median_altitude_rate_m_per_day"].is_null().all():
        st.info(
            "No altitude rates yet: each satellite needs a second element set at least six "
            "hours after its first, so they appear over the first hours after a start."
        )
    else:
        st.altair_chart(
            live.history_chart(history, "median_altitude_rate_m_per_day", "m/day", dark=dark),
            width="stretch",
        )
    st.subheader("Kp, estimated each minute by SWPC")
    st.altair_chart(live.weather_chart(history), width="stretch")


def _value(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def _when(value: dt.datetime | None) -> str:
    return "never" if value is None else f"{value:%Y-%m-%d %H:%M} UTC"


st.title("Live drag nowcast")
st.markdown(
    "Each generation's drag over the last day, from element sets and space weather "
    "as they are published. **This is a nowcast, not the research result**: it answers "
    "*what is happening now*, with provisional inputs."
)
nowcast()
with st.expander("How to read this"):
    st.markdown(
        """
- **BSTAR** is Space-Track's fitted drag term, on every element set, so it is live from
  the first message -- but fitted, so it absorbs orbit-model error as well as air density.
- **Altitude change** compares each satellite's element sets at least six hours apart. It
  fills in over the first hours after a start. Negative is falling. Satellites under
  thrust (orbit rising faster than drag allows) are counted but left out of the median.
- **Generations fly at different heights.** v2-mini DTC flies far lower than the rest, in
  much denser air, so a higher BSTAR or a faster fall there is not a like-for-like
  comparison. The batch explorer compares within an altitude shell; this does not.
- **Space weather is SWPC's quick-look**: the 1-minute Kp is provisional, and Dst is
  Kyoto's quick-look index. The batch path uses NASA OMNI, which is final but a week late.
- `unknown` is mostly satellites launched in the last two months, which GCAT has not yet
  labelled, and which are still raising their orbits.

Data: Space-Track.org element sets (not redistributed -- only these aggregates are shown),
NOAA SWPC space weather (public domain), GCAT generation labels (CC-BY).
"""
    )
