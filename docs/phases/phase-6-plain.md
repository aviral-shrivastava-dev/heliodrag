# Phase 6 — Watching it live, in plain language

The technical version is [phase-6.md](phase-6.md). This one assumes no
background. Nothing here is simplified into being wrong.

## What this phase is for

Everything before this phase works like a diary written the next morning. Each
day, the pipeline collects yesterday's measurements, tidies them, and adds them
to six years of records. That is the right way to answer the research question,
which is about years, not minutes.

This phase adds something different: a **live view**. It shows how hard the
thin upper air is pulling on each kind of Starlink satellite *right now*, and
what the Sun and Earth's magnetic field are doing at the same moment. It
updates by itself while you watch.

It was optional, and the research does not need it. It was built because a
real data platform usually has both halves, a slow careful one and a fast one,
and this shows the fast one working. The rule it had to obey: **the slow,
careful half must not change at all.**

## The pieces

Think of a **post office**.

- **The sender** (the "producer") collects news from two places. Once an hour
  it asks Space-Track for the latest measured orbit of every Starlink
  satellite. Space-Track asks people not to ask more often, and the settings
  make it impossible to. Every five minutes it asks NOAA's Space Weather
  Prediction Center (SWPC), the US government's space-weather service, for the
  newest readings of the storm indicators.
- **The post office itself** is a program called **Redpanda**. It keeps every
  message in order, in named boxes called *topics*: one box for orbits, one for
  space weather, and one for messages that were **refused**.
- **The reader** (the "consumer") takes messages out, keeps a running summary,
  and every fifteen minutes writes that summary to its own table.
- **The live page**, a new page in the explorer, shows the latest summary and
  refreshes itself every minute.

## What the live page shows

For each generation of satellite, over the last day:

- **BSTAR**, a number Space-Track calculates for every orbit it publishes. It
  goes up when a satellite is being slowed by the air. It is there from the
  very first message. But it is an estimate from fitting a model, so it also
  soaks up the model's own errors.
- **How fast the satellites are sinking**, in metres per day. This compares two
  orbits of the same satellite at least six hours apart. So after a fresh
  start, this column fills in over the first hours, as satellites report a
  second time. Satellites firing their engines to climb are counted, but left
  out of the typical value, because engines are not air.

Beside it: **Kp** (how disturbed Earth's magnetic field is, 0 to 9), **Dst**
(which plunges during storms), and **F10.7** (how bright the Sun is at one
radio wavelength, a stand-in for how much it heats the upper air).

## Decisions worth knowing about

**The post office is the reader's memory.** The reader keeps no notes of its
own. Each time it starts, it re-reads every message from the past week and
rebuilds its summary from scratch. That sounds wasteful, but it takes seconds.
It means a crashed reader can simply be started again, and nothing can be left
half-saved. A test checks that re-reading gives exactly the same summary.

**"The last day" means the last day of measurements, not of clock time.** If
the orbits stop arriving, the page does not quietly go blank. It shows that the
newest measurement is getting old, so you can see something is stuck.

**Bad messages are kept, not thrown away.** An orbit that fails the same checks
the careful half uses, or a reading that is physically impossible (a Kp of 12,
say), goes into the "refused" box with the reason written on it.

**NOAA instead of NASA's OMNI.** The careful half uses OMNI, NASA's combined,
checked space-weather record. But OMNI is put together about a week after the
fact: its newest hour was 17 September when this was built on the 26th. A live
view built on week-old weather would not be live. So, with the owner's
agreement, the live view uses NOAA's quick readings, and the careful half still
uses only OMNI. NOAA's quick readings can be revised later, which the page
says.

**Keeping the careful half untouched was checked by machine, not by promise:**

- the post office's software is a separate optional install, which the careful
  half's container does not include;
- a test starts the careful pipeline and fails if even one piece of the live
  code is loaded along with it;
- the live services only start if you ask for them. Without that, the Docker
  setup was compared line by line with the previous version and is identical;
- none of the existing tests were edited.

## What running it found

**A clock shown in the wrong time zone.** The first chart labelled a time as
"23:50 UTC" when it was really 18:20 UTC. Charts in a web browser use the
viewer's own clock (here, India's, 5½ hours ahead) unless told otherwise. It
is the same mistake found earlier the same day in a different part of the
project. The chart now uses UTC, and a test makes sure it stays that way.

**A hole in a safety check, found by reading the design notes back.** If NOAA
renamed one of its fields, the first version would have quietly produced no
readings and no error. Now a missing field counts as a refused message, with
the reason.

**Numbers too small to show.** BSTAR values are around 0.00001 to 0.001, and
the table showed them as 0.0001 for everything. They are now written like
1.4e-5 (1.4 × 0.00001).

## How it was checked

- **All 384 tests pass:** the 337 that already existed and 47 new ones.
- **The main test of "it updates live":** a reader is started and left running.
  One batch of orbits goes in, and the summary has BSTAR but no sinking speeds.
  A second batch, twelve hours of orbit-time later, goes in. The **same running
  reader** updates the summary to show sinking speeds for every satellite.
  Nothing is restarted.
- **For real, on this computer:** the first request to Space-Track returned the
  latest orbit of all 10,836 working Starlink satellites, none refused. NOAA
  sent 679 readings, then 25 to 28 new ones every five minutes. The live page
  showed a Kp reading taken two minutes earlier. The four extra programs use
  about 750 MB of memory between them.
- **Sinking speeds, live:** an hour later the sender fetched 7,282 new orbits,
  and the running reader's next summary had sinking speeds for 1,546 satellites
  where the one before had none. Again, nothing was restarted.

Those first speeds taught something too. Comparing orbits only hours apart is
noisy: in v1.5, v2-mini and v2-mini-opt, the middle half of the satellites spread from about 40 m a day
down to 50 m a day up. And for the working generations the typical
value was slightly *upward*. That is not the air pushing them up. It is their
engines holding height, plus measurement noise. Only v2-mini DTC, which flies
lowest in the thickest air, was clearly sinking, by about 71 m a day. The live
page shows what is happening; it does not untangle those effects. That is the
research's job.

## What is deliberately missing

- **Any use in the research.** The analysis (Phase 7) will not read the live
  view or NOAA's quick readings.
- **A fair side-by-side comparison.** One generation, v2-mini DTC, flies much
  lower than the rest, in thicker air. The main explorer compares satellites at
  the same height; the live page does not, and says so.
- **A nightly check on NOAA's formats**, like the one Space-Track and NASA
  already get, and **tidying of the live table**, which gains a small file
  every fifteen minutes. Both are listed as future work.

## What is still open

- **Live sinking speeds are noisy**, as above. The drag number Space-Track
  calculates (BSTAR) is the steadier live signal. Whether a longer gap between
  compared orbits would make the speeds usable live is an open question.

- After a fresh start with an empty post office, **sinking speeds take a few
  hours to appear**. Orbits are fetched hourly, and each satellite needs two
  orbits six hours apart.
- **The request counter.** Inside Docker, the sender keeps its own count of
  requests to Space-Track, separate from the one on your computer. At two an
  hour, it is far inside the limit, but the two counts do not see each other.
- **Redpanda's licence.** It is free to use like this, but it is
  "source-available", not fully open source. Any Kafka-compatible post office
  would work instead, with no code changes.
- **The main page's name** in the explorer's menu is *streamlit app*, taken
  from its file name. Renaming it means editing the main explorer file, which
  this phase deliberately left alone.
