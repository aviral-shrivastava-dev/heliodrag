# Phase 7 — The answer, in plain language

The technical version is [phase-7.md](phase-7.md). This one assumes no
background. Nothing here is simplified into being wrong.

## What this phase is for

The whole project exists to answer one question. Starlink has built several
kinds of satellite over the years -- "generations" called v1.0, v1.5, v2-mini,
v2-mini DTC and v2-mini-opt -- which differ a lot in size and weight. When a
storm from the Sun heats and puffs up the thin air high above Earth, satellites
feel more drag and sink faster. **Do the different generations react
differently to the same storm?**

The instructions warned that the obvious way of answering would be badly
wrong, and it is. This phase does it carefully, and then makes sure anyone can
check the work: delete every chart, run one command, and every chart comes
back exactly as it was, down to the last pixel.

## Why the obvious answer is wrong

The obvious approach: for each generation, compare how fast its satellites
sink on stormy days and on quiet days. Done that way, v1.0 comes out far more
sensitive than the others: 112 m a day per unit of storm, against 2 for
v2-mini-opt. That is backwards, because v1.0 is the generation with the least
area per kilogram.

Four things fool it:

- **Engines.** Working Starlinks fire small engines to hold their height, which
  cancels most of the drag we are trying to measure.
- **Retirement.** Old satellites are steered down on purpose. Most v1.0
  satellites are retired, so their "sinking" is mostly deliberate.
- **Height.** The air gets much thinner higher up, so a satellite at 350 km
  feels far more drag than one at 550 km, whatever its design.
- **Timing.** The Sun has an eleven-year cycle. Each generation was launched at
  a different point in it, so the Sun was quieter for some than for others.

## How it was done instead

**Each satellite is compared with itself.** For every storm, take a satellite's
sinking speed in the two days after the storm's worst moment. Subtract its
speed in the quiet days just before. Engines, height and the slow solar cycle
barely change in a week, so they cancel out, and what is left is the storm's
extra pull. It works because a storm hits within hours, faster than the
engines react.

On top of that:

- **Satellites are only compared at the same height**, in 25 km bands.
- **Each satellite's life is split into three parts:** climbing to its working
  height after launch, working, and coming down at the end. Only the working
  part is used, because climbing is engines, not air.
- **Generations are compared on the same storms**, the ones both lived through.
- **Storms whose "quiet days before" held another storm were left out:** 41 of
  121, leaving 80.

**A second, different check.** Satellites that have stopped holding their
height and are coming down were also studied. Their day-to-day speed changes
were compared with day-to-day storm activity. It uses different satellites and
different arithmetic, so if both methods agree, that means more.

**How sure are we?** Every number comes with a range, found by re-running the
analysis 2,000 times on storms picked at random, with repeats. That shows how
much the answer depends on which storms happened to occur. Before any of this
was run on real data, the method was tested on made-up data where the right
answer was known. Its ranges contained the right answer 95.5% of the time,
which is what a 95% range should do.

## What it found

**Yes, for one pair, and in the direction physics predicts.** At about 475 km,
where v2-mini and v2-mini-opt fly together, v2-mini-opt sinks faster during
storms: by 33 m a day more per unit of storm, on the same 27 storms. Seven
comparisons were made in total. Even after allowing for that (the more
comparisons you make, the more likely one looks special by chance), this one
still stands. v2-mini-opt has more area per kilogram (1.59 against 1.20), so
more drag per kilogram is exactly what you would expect.

**No detectable difference between v1.0 and v1.5**, which fly together at
525-550 km. That is not surprising: they are very similar in area per kilogram.

**Most other pairs cannot be compared yet.** v2-mini-opt only began launching
in late 2024, so at some heights it has lived through only about 20 storms,
too few to tell. v2-mini DTC flies alone at 350 km, with nothing to compare it
with at the same height.

**How firm is the one "yes"?** Moving the time windows around changes it:

- with a slightly longer storm window, it holds just as firmly;
- with the quiet days taken a little earlier, it only just misses;
- with a one-day storm window, it disappears.

The one-day window is weak for a known reason: day-to-day speeds zigzag,
because each is the difference between two orbit measurements, and the first
day after a storm catches the zigzag. The difference always points the same
way, but this is **a finding, not a settled fact**. It rests on 27 storms and
will firm up, or fade, as more storms arrive.

**The second check agrees where it can see.** Between 325 and 450 km, the
coming-down v2-mini satellites react to storms more strongly than v1.0 and
v1.5 at the same heights, the same direction as the main result. Higher up,
the second check gives results drag cannot produce -- satellites apparently
rising during storms -- which says its assumptions fail there, probably
because of deliberate steering. That is reported as it is.

## Publishing it

- **Every chart and table is now in the project**, in `analysis/figures/`, with
  a list of fingerprints (`SHA256SUMS`). They show totals and averages per
  generation, height and storm, never individual satellites' raw data.
- **The raw orbit data is not published.** Space-Track's rules do not allow
  it. [`DATA_AVAILABILITY.md`](../../DATA_AVAILABILITY.md) says what is
  published, what is not, and how to get the rest yourself.
- **`CITATION.cff`** tells others how to credit the work and its data sources.
- **The time span is frozen** at 1 January 2020 to 16 September 2026. The
  charts must not change just because new data arrived overnight.

## How it was checked

- **The main acceptance test:** the whole `analysis/figures/` folder was
  deleted and the one command was run. All 15 files came back identical.
- **The same test runs automatically** on every change, on test data.
- **The method was checked on made-up data** where the answer was known.

## What is deliberately missing

- **Turning these numbers into air density.** That needs each satellite's true
  drag area, which is not public.
- **A single "ranking" of every generation.** The data only supports
  comparisons at the same height.

## What is still open

- **Engines differ between generations.** Newer satellites have engines more
  than twice as strong, so part of a storm's effect may be undone faster. That
  makes comparisons within a family (v1.0 with v1.5, v2-mini with v2-mini-opt)
  fairer than across families.
- **"Area per kilogram" is a rough stand-in.** It uses the solar panels'
  length and the satellite's weight. The real drag area depends on which way
  the satellite is turned, and nobody outside SpaceX knows that.
- **More storms are needed** for v2-mini-opt, the newest and largest group.
- **"No one has done this before"** means none was found in a search of
  published work, not that none exists.
