# Phase 3 — Orchestration, in plain language

The technical version is [phase-3.md](phase-3.md). This one assumes no
background. Nothing here is simplified into being wrong.

[Phase 1](phase-1-plain.md) fetched the data; [Phase 2](phase-2-plain.md) turned
it into usable tables. This phase makes the whole thing run **by itself**.

## What this phase does

Until now everything was run by hand: fetch this, then clean that, then check
the other. That works exactly once, and only if you remember the order.

This phase hands the whole sequence to a scheduler — Dagster — which knows:

- what has to happen before what
- which days have already been done and which have not
- what to do when something fails
- whether the result looks healthy afterwards

The important design rule: **the scheduler does not contain any of the work.**
Every step is three or four lines calling the same functions you can run from
the command line. If Dagster were removed tomorrow, the pipeline would still
run. That is deliberate — it is the difference between using a tool and being
married to one.

## The awkward bit at the heart of this phase

The plan asks for the work to be organised **one day at a time**. That is the
right unit: each day's data is a separate thing you might want to redo.

But there is the speed limit again. If fetching each day meant its own request,
six years would be about **eighty thousand requests** — over a week of solid
waiting.

So the two things are separated:

- **Filing** happens one day at a time, as planned.
- **Fetching** happens in wide chunks — three months at a go.

Dagster supports exactly this. You ask it to rebuild 2020 to today, and instead
of starting two thousand separate jobs it starts **one**, which looks at the
whole span it has been given and fetches efficiently — while still recording
every individual day as done.

The difference is roughly half an hour versus eleven days. There is a test
whose only job is to make sure nobody removes that setting, because removing it
would change no result at all — it would just make the pipeline unusable.

## What the picture looks like

Dagster draws the pipeline. Left to right:

```
catalogue ─┐
           ├──> views ──> cleaned tables ──> answer tables
weather ───┤
elements ──┘
```

Four coloured groups in order: **bronze** (raw), **warehouse** (the bridge),
**silver** (cleaned), **gold** (final answers). Twenty boxes, eighty automatic
checks.

One arrow is worth explaining. **Elements depend on the catalogue**, because the
list of which satellites to ask about comes from the catalogue. Asking for a
satellite that had not launched yet wastes a request you cannot afford.

Another was needed to fix a real ordering bug. The cleaning step reads through
"views" — a kind of live window onto the raw data — and those windows have to be
rebuilt whenever new data lands. Originally both the window-rebuilder and the
cleaning step just waited for "raw data", which meant Dagster was free to run
them in **either order**. Half the time the cleaning would read yesterday's
window. Putting the windows explicitly in the middle fixed it.

## What happens when something breaks

Two safety nets, at different heights.

**The low one** is inside the code that talks to the websites: if a single
request fails, it waits and tries again.

**The high one** is around the whole day's work: if that fails — the login
expired, the network dropped, the site went down — Dagster waits and runs the
whole thing again, up to three times, waiting longer each time.

Both are tested, and the tests are deliberately blunt:

- One test builds a step that **fails twice on purpose** and then works. The
  run must succeed. It does.
- Another builds a step that **never works**. It must give up after the right
  number of tries rather than retrying forever. It does.

## The four health checks

Passing is not the same as being right. A run can succeed perfectly while
fetching nothing at all. So there are four checks that look at the *data*, not
at whether the code ran:

| Check | Question it asks |
| --- | --- |
| Elements are fresh | Is the newest reading less than three days old? |
| Weather is fresh | Has space weather kept up with the satellite data? |
| Enough rows | Is the final table actually populated, and mostly usable? |
| Not full of blanks | Are the columns the science needs actually filled in? |

On the verification run these reported *"2,192,356 rows, 91% usable"* and *"all
within tolerance"*.

They are set to **warn, not fail**. A slightly stale table is worth knowing
about; it is not worth halting everything over.

## One command

```
starlink-drag backfill
```

That rebuilds everything from 2020 to yesterday. It finished with a clean exit.

Underneath it does three things in order — catalogue, then the dated sources,
then the cleaning — because the scheduler refuses to mix "things organised by
day" and "things not organised by day" in a single instruction. One command for
you; three steps underneath, always in the same order.

## Three bugs this phase found

Putting something on a schedule means running it repeatedly, and **repetition
finds bugs that a single careful run never will.**

**Re-running broke two checks — correctly.** In Phase 1 the raw layer was
changed to "always add, never replace", with duplicates removed later during
cleaning. That was done for the satellite elements. It was **not** done for the
space weather or the catalogue. Nobody noticed, because nothing had been fetched
twice yet. The moment this phase re-ran a range, duplicates appeared and two
checks failed. Both now get the same deduplication treatment.

**The cleaning tool was writing to the wrong file.** Dagster runs it from a
different folder than the manual command did, so a relative path like
`data/warehouse` pointed at two different places depending on who started it.
Now it is always given the full path.

**A wildcard never arrived.** The command said "rebuild `*`" — everything. But
the shell helpfully replaced the `*` with a list of every file in the folder
before the scheduler saw it, and the scheduler complained about being handed
`README.md`. It now names the four groups explicitly, worked out from the
pipeline itself so it cannot go out of date.

## What was checked

| Check | Result |
| --- | --- |
| Pipeline definition valid | yes |
| One-command rebuild, start to finish | **clean exit**, all three steps |
| All cleaning checks | 89 passed, 0 failed |
| Whole test suite | **212 passed**, no internet |
| A failing step recovers | proven: fails twice, then succeeds |
| A hopeless step gives up | proven: stops after the right number of tries |
| The picture is readable | looked at it; four groups, correct order |

## What is still open

**The daily alarm clock has never actually gone off.** It is set for 6am and the
definition is valid, but proving it fires means leaving the scheduler running
for a day. Phase 4 does that.

**The speed-limit guard is an agreement, not a lock.** The two steps that talk
to Space-Track are marked as "must not run at the same time", and today they
never do because the one command runs them in order. But that is a fact about
how it happens to be run rather than something enforced. If someone later makes
it run things in parallel, the mark alone will not stop them.

## What happens next

Phase 4: hardening. More test coverage, a nightly automatic run, an emergency
handbook for when things break, and the cloud setup.
