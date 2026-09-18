# What this project is — in plain language

The technical version of this document is [overview.md](overview.md). This one
assumes you know nothing about satellites, space weather or data engineering. It
is not a simplified story — every fact here is the real one.

## The one-line question

> Starlink satellites come in several different models. Do the different models
> fall out of orbit at measurably different speeds when the Sun does the same
> thing to all of them?

Everything in this repository exists to answer that from real measurements
rather than from a calculation on paper.

## Why a satellite falls at all

Space is not a perfect vacuum. At 550 km up — roughly where Starlink sits —
there is still a whisper of atmosphere. Unbelievably thin: something like a
million-billion times less air than the room you are sitting in.

That sounds like nothing. It isn't, because of speed. A satellite at that height
is travelling about **7.6 kilometres every second** — London to Paris in about
45 seconds. Ploughing through even a whisper of air at that speed produces a
real, continuous backward push.

That push is called **drag**. Drag steals energy from the orbit, and an orbit
with less energy is a smaller orbit. So the satellite spirals very slowly
downward, and if nothing intervenes it eventually burns up in the thicker air
below. Starlink satellites fight this with ion thrusters. Left alone, one would
fall out of the sky within a few years.

## Why the Sun controls it

This is the part that makes the whole thing a *space weather* problem rather
than a fixed-physics problem.

The Sun does not shine steadily. It has a roughly eleven-year rhythm of activity
— more sunspots, more flares, more ultraviolet and X-ray light. We are currently
living through **Solar Cycle 25**, which began around December 2019 and reached
its busiest period in 2024–2025. That timing is lucky: it covers almost exactly
the years Starlink has existed.

When the Sun gets more active, the extra ultraviolet and X-ray light is absorbed
by the very top of the atmosphere and **heats it**. Hot air expands. The whole
upper atmosphere puffs outward like bread rising.

So at a fixed height of 550 km, an active Sun means there is suddenly *more air
in the way* than there was a week ago. Same satellite, same altitude, more drag,
faster fall.

The effect is not subtle. During a strong geomagnetic storm the density of air
at satellite altitude can multiply several times over within hours. In February
2022, SpaceX lost most of a batch of 49 newly launched Starlinks to exactly
this: a storm puffed the atmosphere up while the satellites were still in their
low initial orbit, and they could not climb out of it.

We track the Sun's mood with four numbers, all published daily and free:

| Number | What it measures, in one line |
| --- | --- |
| **F10.7** | How brightly the Sun shines in radio waves. A stand-in for the ultraviolet light we cannot easily measure from the ground, and so for how puffed-up the atmosphere is. |
| **Kp** | How disturbed Earth's magnetic field is right now, on a 0–9 scale. |
| **Ap** | The same disturbance, averaged over a day, on a scale you can do arithmetic with. |
| **Dst** | How badly a storm has weakened Earth's magnetic field near the equator. Goes strongly negative during a big storm. |

## Why the model of satellite should matter

Drop a hammer and a feather in air and the hammer wins — not because it is
heavier in itself, but because of the **ratio** between how much mass it has and
how much air it has to shove out of the way.

Engineers call that ratio the **ballistic coefficient**: mass divided by
(frontal area × how un-streamlined the shape is). A high ballistic coefficient
means "heavy for its size" — it barrels through. A low one means "big and
light" — the air pushes it around easily.

Starlink's models differ a great deal on exactly this ratio:

| Model | Roughly how heavy | Note |
| --- | --- | --- |
| **v1.0** | ~260 kg | The original operational design, from 2019. |
| **v1.5** | ~300 kg | Adds laser links between satellites. |
| **v2-mini** | ~800 kg | Much heavier, and much larger solar arrays. |
| **v2-mini DTC** | ~800 kg plus | Carries a big extra antenna for connecting to phones. |

A v2-mini is roughly three times the mass of a v1.0, but it is also physically
much bigger. Whether it ends up *more* or *less* vulnerable to a solar storm
depends on which of those two grew faster — and that is a question for
measurement, not something you can settle by looking at a photograph.

So: **when a storm hits, do the fleets separate?** Does one generation visibly
lose altitude faster than another?

## Why this is hard, and where people go wrong

The naive version of this analysis takes the decay rate, correlates it against
F10.7, splits the result by generation, and announces a finding. That version is
wrong, and it is wrong in three specific ways.

**1. Height.** Air thins out rapidly as you go up. A satellite 30 km higher than
another feels noticeably less drag. If v2-minis happen to sit in slightly
different altitude shells than v1.0s, you would measure the *altitude*
difference and report it as a *generation* difference.

**2. Satellites that are still climbing.** A freshly launched Starlink is
deliberately raising its orbit with its thruster. It is going *up* while drag
pulls it *down*. Including those mixes "how SpaceX chooses to fly its fleet"
into a measurement of "how physics treats the hardware".

**3. Time.** This is the nastiest one. The Sun grew steadily more active across
the whole period. The newer generations also launched later. So "is a v2-mini"
and "existed during high solar activity" rise and fall in step — they are
tangled together. A method that cannot separate them will credit the hardware
for something the calendar did.

These are called **confounders**: things that vary alongside what you care about
and can masquerade as it. Handling them is most of the scientific work here, and
it is why the analysis in Phase 7 explicitly controls for altitude shell,
orbit-raising status and the overall solar-cycle trend instead of just drawing a
scatter plot.

## Where the numbers come from

Two sources, both real, both public, neither invented.

**Space-Track.org**, run by the US Space Force, publishes the tracked orbit of
every object in space. For each satellite, on most days, there is a record of
its orbit shape and of how many times a day it circles the Earth. As drag
shrinks the orbit, the satellite circles *faster* — so that "times per day"
figure creeping upward is the fingerprint of decay. That is the core
measurement.

Space-Track needs a free login and enforces strict limits: fewer than 30
requests a minute and fewer than 300 an hour. Exceed them and the account is cut
off. It also comes with a US-government agreement that permits *using* the data
but not handing out copies of it.

**NASA OMNI**, served by NASA's Space Physics Data Facility, publishes the four
space-weather numbers above. No login, no limits, public domain.

## What is actually being built

Getting those two sources into a form anyone can trust means roughly six years
of daily records for thousands of satellites — millions of rows — fetched under
a rate limit, cleaned, joined and checked. Doing that by hand once is possible.
Doing it identically every day, and being able to prove you did, is not.

So the repository builds a **pipeline**: a machine that fetches, tidies,
combines and checks, the same way every time, without a human remembering the
steps.

Three rules make its output trustworthy, and they show up everywhere in the
code.

**One day at a time.** Work is cut into daily chunks called *partitions*. Each
day is fetched, stored and processed on its own.

**Redoing a day changes only that day.** If 3 March fails, or arrives wrong, you
re-run 3 March and nothing else moves. The word for this is **idempotent**:
doing it twice gives the same result as doing it once, like pressing a lift
button that is already lit.

**Never edit the original.** Whatever Space-Track sent is kept exactly as
received, stamped with when it arrived, and never altered. All cleaning happens
on a copy. If a cleaning step later turns out to be wrong, the original is still
there to redo it from. That untouched layer is called **bronze**, the cleaned
layer **silver**, and the final answer tables **gold**.

## How the work is organised

The project is built in eight numbered phases, 0 through 7, one at a time, each
reviewed before the next begins. Phase 0 — the workshop, built before any
pipeline exists — is described in [phase-0-plain.md](phase-0-plain.md).
