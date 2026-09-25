# Phase 4 — Hardening, in plain language

The technical version is [phase-4.md](phase-4.md). This one assumes no
background. Nothing here is simplified into being wrong.

Phases 0–3 built the pipeline and put it on a schedule. This phase is about what
happens when **nobody is watching**.

## The bug that justifies the whole phase

The six-year data collection was started. It worked through the first three
quarters of 2020, and then wrote this:

```
2020-09-27..2020-12-26 -> 0 rows, 0 partitions
```

No error message. No warning. It carried on and reported success.

Afterwards, asking Space-Track for exactly that period by hand returned
**30,015 records** — from one batch of 200 satellites out of 12,865. The data was
there all along.

Here is what happened. Space-Track has a speed limit. When you push close to it
for a long time, it does not say "slow down". It replies **"here you go"** — and
sends back an empty list.

So the pipeline asked "what happened in October 2020?", was told "nothing", and
wrote down: nothing happened in October 2020.

**This is the worst kind of bug.** It does not crash. It does not look wrong. It
just quietly puts a three-month hole in the data, and every chart and statistic
built on top would have been computed around that hole without anyone knowing.

### The fix

The pipeline now notices when a stretch of time comes back empty **while the
stretches either side came back full**. That contrast is the giveaway: the
atmosphere does not stop existing for a quarter.

When it spots that, it **fails on purpose**, which makes the scheduler retry.

Crucially it does *not* complain about genuinely empty periods. If you ask for
data from before the first satellite launched, empty is the right answer, and
failing on that would be its own bug. The signal is the contrast, not the
emptiness. There are tests for both halves.

## The other thing that fails silently

Imagine NASA renames one of the space-weather measurements. Nothing breaks. The
pipeline looks for the old name, does not find it, and records a blank. Every
calculation continues, quietly averaging blanks.

Every check stays green. The answer rots.

So there is now a command that asks both websites: **"do you still have the
fields we read?"**

```
ok    nasa.omni: 55 parameters declared, all 5 we read are present
```

It runs every night. If something has been renamed or removed it says so, by
name, and fails.

It also watches the "no measurement" markers. NASA writes `999.9` to mean "we
did not measure this". If they ever changed that to a different number and we
did not notice, a fake solar-brightness reading of 999.9 would slide into the
science — and it would not look obviously wrong in an average.

If nobody has set up a Space-Track account, the check **skips** rather than
failing. A contributor without credentials should not get a red mark for it.

## Testing the maths exhaustively

The orbital physics — the part where a mistake would silently flip every result
— is now covered by tests **100%**. Every line, every branch.

More importantly, the bar is **enforced**, not just measured. There is a command
that fails if coverage ever drops below 90%, and the nightly run uses it. A
number you only look at drifts; a number that fails the build does not.

## Testing the whole cleaning process, in one second

There is now a test that runs the **real** cleaning process — all of it, every
check — against a tiny made-up dataset built inside the test. It finishes in
about a second.

The clever bit: the made-up satellites use **real ID numbers** taken from the
published hardware catalogue. So the step that works out which model each
satellite is gets genuinely exercised, while no real satellite data is involved
— the orbits are invented on purpose.

Six things it proves that a merely "green" run would not:

- the tables actually have rows in them (a green run over empty tables proves nothing)
- satellites whose orbits are shrinking are reported as **falling**, not rising
- the one storm in the fake data is found, on the right day
- every day has its weather attached
- and — the important one — **fetching the same data twice does not double-count it**

That last one matters because of a trade-off made back in Phase 1: the raw layer
always adds and never replaces, with duplicates removed later. This test proves
the removing actually works, rather than assuming it.

## The nightly check

Two separate jobs, because they need different things.

**The first needs nothing at all** — no passwords, no internet. It re-runs every
test plus the full cleaning process. It exists because software rots even when
nobody touches it: something it depends on releases a new version and quietly
behaves differently.

**The second needs the internet**, and asks the two websites whether they still
look the same. If one is briefly down, it reports rather than failing.

**Neither one collects data, deliberately.** The machine that runs these checks
is wiped after every run, so there is nowhere to keep anything. A nightly job
that pretended to be keeping the database up to date would be worse than one
that honestly does less. That becomes possible once the storage moves to the
cloud — which is the next piece.

## Cloud storage and a local rehearsal

Two pieces of setup.

**The cloud storage definition** creates the bucket the data will live in. The
important part is the **cleanup rules**. Because the raw layer always adds and
never replaces, every re-run leaves the old copy behind forever, and nothing
deletes it. Without a cleanup rule the storage bill grows every time you re-run,
not every time you get new data. A second rule cleans up half-finished uploads,
which a killed collection run leaves behind — invisible, but still charged for.

**The local rehearsal setup** runs a miniature version of the same cloud storage
on your own machine, plus the scheduler. Because the miniature speaks the same
language as the real thing, the only difference between "on my laptop" and "in
the cloud" is an address and a password.

It also runs the scheduler's **timekeeper** as a separate piece. That is what
was missing in Phase 3, where the daily alarm was set but had never actually
gone off.

## The emergency handbook

There is now a handbook for when things go wrong: what to do about login
failures, hitting the speed limit, a website changing shape, one day's data
failing, and how to safely re-run.

**Every entry in it is something that genuinely went wrong while building this**
— the silent empty quarter, the login expiring halfway through, the collection
run being killed, the Windows filename-length limit, the trap of reading the
data folder directly instead of asking the catalogue.

It opens with the single most useful fact: **re-running is almost always safe.**

## What was checked

| Check | Result |
| --- | --- |
| Whole test suite | **232 passed**, no internet |
| Physics coverage | **100%**, and the bar is enforced |
| Full cleaning run on fake data | green, ~1 second |
| "Do the websites still look right?" | run live: all 5 measurements present |
| Container setup file | valid, 4 pieces |
| The empty-stretch alarm | two tests: catches a real gap, ignores a genuine one |

## What I could not check at first, and what happened when I did

When this phase was built, three things could not be tried on this machine, and
the first version of this page said so. All three have since been tried, and
trying them turned up **nine more mistakes** that just reading the files had
missed.

**The nightly check now runs, and ran by itself for six days in a row.** The
project was put on GitHub on 19 September. The automatic checks failed twice at
first, because they assumed two files existed that only ever existed on my
computer. Once that was fixed, the nightly check passed every night from 20 to
25 September without anyone touching it.

But part of it had been quietly doing nothing. The step that asks Space-Track
"do you still send data in the shape we expect?" needs a password, and GitHub
did not have one — so every night it said "skipping" and moved on, and the
overall result still showed green. When the password was added, its very first
real run failed.

The reason was almost funny. To ask Space-Track about its data, the check always
asked about one particular satellite, STARLINK-1007. That satellite **fell out of
the sky and burned up in October 2024**, two years before the check was written.
A satellite that no longer exists has no new data, so the check could never have
passed. It now picks five satellites that are still up there, fresh from the
catalogue every time, and it passes.

The lesson: a check that skips looks exactly like a check that passes, unless
somebody reads what it actually said.

**The cloud storage setup has now been checked by its own tool,** and the tool
found two mistakes: one rule was missing a required part, and one setting was
spelled the way a different Cloudflare tool spells it. Both are fixed.

**The container setup has now been started, and it works,** after four fixes:
one of the programs had moved to a different download site; a file the build
needed was not being copied in; the web page part was listed as a
developer-only tool, so it was left out; and — the important one — every build
was copying the entire 5.8 GB data folder into the container system, including
data that is not allowed to be shared.

## What still has not been checked

**The cloud storage has never actually been created.** The setup is now
correct on paper, but creating it for real needs a Cloudflare account, which
this project does not have yet.

**The scheduler inside the containers has not been seen doing its job.** It
starts and stays running, but nobody has yet watched it trigger the daily run,
and it logged one warning that has not been looked into.

## A smaller bug, worth mentioning

The first attempt at the six-year collection failed instantly with "could not
find 18 September". The command was working out "yesterday" using the local
clock, while the scheduler organises days in **UTC** — and only counts a day once
it has fully finished. At 2am local, "yesterday" was two days ahead of the newest
real day.

It now **asks** the scheduler what the newest day is instead of working it out.
That removes the whole category of mistake rather than this one instance.

## The six-year collection, honestly

This is still the outstanding item. A full run from 2020 to today is about
**1,755 requests**, and the speed limit allows 300 an hour. That is close to
**six hours**, nearly all of it waiting.

Two attempts have been killed partway through when the session ended — which is
exactly what the handbook's "the run died" entry is for.

It can be made faster: asking about 500 satellites per request instead of 200
would cut it to roughly two and a half hours. That is a change I have not made
while a run is in progress.

## What happens next

Phase 5: the web app you can click around, and the front-page documentation —
the diagram, the one-command setup, and what it all costs to run.
