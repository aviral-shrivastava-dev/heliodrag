# Phase 2 — Transformation, in plain language

The technical version is [phase-2.md](phase-2.md). This one assumes no
background. Nothing here is simplified into being wrong.

[Phase 0](phase-0-plain.md) built the workshop; [Phase 1](phase-1-plain.md)
fetched the raw data. This phase turns it into something you can actually ask a
question of.

## What this phase does

Phase 1 left us with millions of raw records saying "at this moment, this
satellite was going round the Earth this many times a day". True, but useless on
its own.

This phase turns that into a table where each row says:

> On **this day**, satellite **X** — which is a **v1.5** — was at **547 km** and
> **losing 11 metres a day**, while the Sun was **this active** and there was
> **this much of a magnetic storm**.

That is a row you can do science with.

## First, a plumbing problem

The raw data is stored in a format called Iceberg. The tool that does the
cleaning, DuckDB, is supposed to be able to read Iceberg directly.

It cannot — not here. It needs a pointer file that our storage tool does not
write, and it cannot open the file addresses Iceberg records on Windows (the
same malformed-address problem from Phase 1, where the drive letter goes
missing). The documented workaround does not help either.

The way round it: **ask the catalogue which files are current, then hand DuckDB
that exact list.** It works, and it is better than the obvious alternative of
"just read everything in the folder", because the folder also contains old
withdrawn copies.

Because new data arrives constantly, that list is rebuilt before every cleaning
run rather than written down once.

## Working out which satellite is which model

This is the hardest factual problem in the project, and it deserves explaining
because it is where a mistake would do the most damage.

The research question compares Starlink **models**. So we need to know which
model each of 12,440 satellites is. And here is the problem: **the satellite
catalogue does not say.** Space-Track will tell you where a satellite is, but not
what it is.

One public catalogue does record it — Jonathan McDowell's GCAT, which lists the
spacecraft type and its mass. That is the source used here.

GCAT states the newer models outright. For the three **older** models it just
says "Starlink" for all of them. So those three are separated by **weight**:

| Weight at launch | Model |
| --- | --- |
| under 240 kg | v0.9 |
| 240–280 kg | v1.0 |
| over 280 kg | v1.5 |

That is an inference, not a fact the source states, so every satellite labelled
that way is **marked as inferred**. The newer ones are marked as stated. The
analysis can therefore treat the two differently, and nobody later has to guess
how confident to be.

### Why we can believe the weight trick

The split was done using **only weight** — the code never looked at launch
dates. Then the launch dates were checked afterwards:

| Model | Count | First launch |
| --- | --- | --- |
| v0.9 | 60 | 24 May 2019 |
| v1.0 | 1,678 | 11 Nov 2019 |
| v1.5 | 2,938 | 14 Sep 2021 |
| v2-mini | 2,760 | 27 Feb 2023 |
| v2-mini DTC | 663 | 3 Jan 2024 |
| v2-mini Optimised | 4,341 | 25 Nov 2024 |

Every one of those dates is a publicly known milestone — the single prototype
batch of exactly 60 in 2019, the first operational launch, the first with laser
links, the first of the new larger design, the first that talks to phones.
Sorting purely by weight reproduced the entire history in the right order
without being told it. That is about as good as this kind of check gets.

The weight and size difference is also the whole reason the question is worth
asking: the older models weigh ~260 kg with a 9-metre wingspan, the newer ones
575–960 kg with a **29-metre** wingspan. Three times wider.

One more thing worth noting: the public catalogue is free to reuse with credit,
unlike the satellite position data. So this lookup table **is** included in the
repository and anyone can rebuild it.

## Turning orbits into "metres lost per day"

A satellite's orbit is reported as "times around the Earth per day". As it
falls, its circle gets smaller, so it goes round *faster*. Counterintuitive, but
that speeding up is the fingerprint of falling.

"Revolutions per day per day" is not a number anyone can picture, so it is
converted into **metres of altitude lost per day** using a 300-year-old piece of
physics (Kepler's third law).

Getting this wrong would quietly ruin everything, so it is checked against
things that are true regardless of our code:

- A satellite going round once per day comes out at 42,164 km — the known height
  of a TV satellite.
- One going round 15.5 times a day comes out at ~420 km — the known height of
  the Space Station.
- Where real data is available, our numbers match the ones Space-Track worked
  out themselves, to within a kilometre.

### Choosing which reading counts as "the day"

Readings arrive at irregular times. Two choices were needed:

**Which reading is "today's"?** The last one of each day, so the gap between
consecutive days stays close to 24 hours.

**What if a satellite goes quiet for a week?** Then the change is divided by the
*actual* gap. Treating a five-day gap as one day would make the satellite look
like it was falling five times slower than it was.

## Not throwing data away

There are good reasons to exclude some rows: a satellite firing its thruster is
not measuring the air, and a reading with a week-long gap is unreliable.

But the rows are **not** deleted. Each one is **labelled**: "was manoeuvring",
"gap was sensible", "usable for the main comparison". The analysis chooses what
to include, and anyone can see exactly what each exclusion costs.

Across all of 2024: 2,192,356 rows, of which 1,989,855 — about 91% — are usable.
The rest are set aside as manoeuvring, as having an unusable gap, as physically
impossible, or as belonging to a satellite whose model is unknown.

## A test that found real physics

There was a safety check saying no satellite should change altitude by more than
50 km in a day. Fourteen rows broke it.

They were all real. Every one was a satellite between **137 and 298 km** — in its
final hours, falling into the atmosphere. Down there the air is thick enough to
take 140 km off an orbit in a day.

The check was wrong, not the data. So it was replaced with one that encodes the
actual physics: a huge drop is **fine below 300 km and impossible above it**. A
genuine re-entry now passes and an impossible number at 550 km still fails.

There is a second check of the same kind: falling faster must always mean
getting lower. If those two ever agreed in sign, a minus sign would have gone
missing from the conversion — which would silently flip every result in the
project upside down.

## What was checked

| Check | Result |
| --- | --- |
| Full cleaning run, with all its tests | **87 passed, 0 failed** |
| Lineage diagram | complete; every table documented |
| Data dictionary | generated automatically from the models |
| Whole Python test suite | 199 passed, no internet |
| The physics in SQL vs the physics in Python | 57 checks, agreeing to 12 decimal places |
| Model lookup table | 12,440 satellites, 100% labelled |
| Raw records landed | 5,964,131 over 366 days, 1 rejected |

That single rejected record is worth a mention, because it is exactly what the
quarantine exists for. One satellite arrived with an orbit whose lowest point was
**339 km below the surface of the Earth** — impossible for something still
flying. It was set aside with a note saying why, and the other 5,964,130 records
carried on.

That last one matters: the orbital maths is written **twice** — once in Python
where it is easy to test, once in SQL where it is fast enough for millions of
rows. Two copies of the same formula drift apart over time, so there is a test
that reads the SQL, runs it, and checks it still agrees with the Python.

## A first look at the answer — and why you must not believe it

The finished table covers all of 2024: **2.19 million satellite-days across
7,169 satellites**, 91% of them usable. The storm detector found **18 storms**,
the worst reaching −406 — which is the great storm of 10–11 May 2024. It found
that on its own, without being told the date.

So, do the models differ? Here is the crude answer, averaging how fast each model
fell in the two days after a storm:

| Model | Metres lost per day | Size-to-weight | Average height |
| --- | --- | --- | --- |
| v1.0 | −4,247 | 0.33 | 534 km |
| v1.5 | −3,260 | 0.28 | 549 km |
| v2-mini | −2,253 | 1.20 | 504 km |
| v2-mini DTC | −854 | 0.92 | 354 km |

**This is wrong, and it is worth understanding why.**

It says the *smallest, densest* satellites fall fastest. That is backwards — a
big light satellite should be pushed around more, not less. It also says the
lowest-flying group falls slowest, which is backwards too, because the air is
thicker lower down.

The explanation is that **63% of the v1.0 fleet has been retired**. Those
satellites are being deliberately flown down to burn up. A satellite under orders
to descend falls far faster than any storm could push it, and averaging those in
drowns out the real effect entirely.

This is exactly the trap described at the start of the project. The table is
built so the trap can be avoided — every complicating factor is there as a column
to hold steady — but doing that properly is a statistics job, and that is Phase 7.

## The problem that could sink the research question

This is the most important thing in this document.

**Starlink satellites do not simply fall. They actively hold their altitude.**
Each one has an ion thruster, and on station it fires continuously to cancel out
exactly the drag we are trying to measure.

You can see it in the results. The typical v1.5 satellite in early 2024 changed
altitude by **+0.1 metres per day** — essentially zero, and very slightly
*upward*. Not because there is no drag, but because the drag is being cancelled
as fast as it happens.

So a large part of what we are measuring is SpaceX's autopilot, not the
atmosphere.

The plan for this project listed three things that could mislead us — height,
satellites still climbing to their final orbit, and the Sun getting busier over
the years. This is a fourth, and in some ways the worst: the others *distort* the
answer, while this one can **erase** it.

There are two honest ways forward, and Phase 7 has to pick:

1. **Look only during storms.** When a storm hits, drag briefly exceeds what the
   thrusters can cancel, so a real signal pokes through the control. This is why
   the storm table exists.
2. **Look only at satellites that are not being controlled** — ones that have
   failed, are being retired, or have not yet climbed to their working orbit.

Both are possible with what has been built. Flagging it now is much better than
discovering it at the end.

## Two smaller open questions

**The research question misses a model.** It names four, but the catalogue has a
fifth — "v2-mini Optimised" — and at 4,341 satellites it is the **largest group
of all**. It only started launching in late 2024, so it barely appears in 2024
data, but any longer run will be dominated by it.

**425 satellites have no model label.** Space-Track lists them, the public
catalogue has not classified them yet. They are kept and marked "unknown" rather
than quietly dropped — about 3% of the fleet.

## What happens next

Phase 3: putting this on a schedule, so it updates itself daily and can rebuild
any single day on demand, with automatic checks that the data arrived, is fresh,
and is the right size.
