# Phase 1 — Ingestion, in plain language

The technical version is [phase-1.md](phase-1.md). This one assumes no
background. Nothing here is simplified into being wrong.

If you have not read [what this project is](overview-plain.md), start there.
[Phase 0](phase-0-plain.md) built the workshop; this is the first phase that
does real work.

## What this phase does, and does not

It **fetches** data from two websites and **stores** it. That is all.

Nothing is calculated. No question is answered. The satellites are not yet
compared to one another. This phase exists so that everything after it can start
from data that is complete, correct, and the same every time you look.

The rule for this layer: **write down exactly what arrived, and change nothing.**
Cleaning happens later, on a copy. If a cleaning step turns out to be wrong, you
need the untouched original to go back to.

## The speed limit, and why the obvious answer is wrong

Space-Track lets you ask fewer than 30 questions a minute, and fewer than 300 an
hour. Break that and they don't slow you down — they **block the account**. In
the middle of collecting six years of data, that is a very bad day.

So you need something that paces the asking. The standard tool is called a
**token bucket**, and it works like this: you have a jar holding 29 coins. Each
question costs a coin. The jar refills at 29 coins per minute. No coin, you wait.

That sounds right. It isn't, and the reason is worth following.

Suppose you spend all 29 coins at **11:59:59**. One second later the minute
turns over, the jar refills, and you spend 29 more at **12:00:01**. You have now
asked **58 questions in two seconds** — while never once having an empty jar at
the wrong moment.

The rule was never "29 per clock-minute". It was "29 in *any* sixty seconds",
and a jar that refills on a schedule cannot see across that boundary.

So this project does something simpler and more obviously correct: **keep a list
of when you last asked.** Before asking again, count how many of those times
were in the last sixty seconds. If it is 29 already, wait until the oldest one
is more than a minute old. Same for the hourly limit, checked at the same time.

It cannot go over the limit, because going over would require the count to be
wrong. And it is still fast when you have been idle — if you have asked nothing
for an hour, you may fire off 29 immediately, because you genuinely have not
used them.

The brief for this project asked for a token bucket. This is one of the places
where following the instruction exactly would have produced the thing the
instruction was trying to prevent, so it was built differently and the reasoning
written down in [ADR-0003](../adr/0003-sliding-window-rate-limiter.md).

## Asking the right question saves weeks

There are **12,865** Starlink objects on record. We want six years of daily
positions. The obvious approach is to ask, day by day: "where was everything on
1 March 2024?" That is one question per day per group of satellites — tens of
thousands of questions for a single year. At fewer than 300 an hour, that is
**weeks** of waiting.

But you can also ask: "where were these 200 satellites for the whole of 2024?"

That was tested against the real service. One such question returned **90,086
records in 25 seconds**, with no limit on how much came back. A whole year for
every Starlink takes about **65 questions** that way.

Same data. Two hundred times fewer questions. Half an hour instead of weeks.

So the rule became: **ask wide, store narrow.** One request covers many
satellites over many months; the answer is then chopped into one folder per day
before being saved.

## Everything arrives as writing, not as numbers

When Space-Track sends a satellite's orbit, the speed does not arrive as the
number 15.06402759. It arrives as the *text* `"15.06402759"` — characters, not a
quantity. The satellite's ID number arrives as text too.

You cannot add up text. So the first thing that happens to every record is
conversion: text into numbers, text into dates. If that step is sloppy, every
calculation afterwards is built on sand.

## The trap that would have poisoned the science

NASA's space-weather records have gaps — hours where an instrument was down or a
measurement was never taken. They do not leave those blank. They write **999.9**.

That is a convention, and it is written down. But nothing stops a program from
reading 999.9 as a real measurement. Do that, and a quiet day with no
observation becomes the brightest Sun ever recorded. Average a month of those in
and the answer is wrong — and, worse, wrong in a way that looks like data.

So the code that talks to NASA converts every one of those markers to "no
value", right at the door, before anything else sees it. There is a test built
from a real day in 1963 where the Sun brightness really is missing, and it
checks that the number never survives.

## When a record is broken, quarantine it

Occasionally a record is wrong. Not unusual — **impossible**. An orbit that
would put a satellite below the ground. An eccentricity saying the orbit is not a
closed loop at all.

Two tempting responses, both bad:

- **Stop everything.** One bad record in ninety thousand throws away the other
  89,999 and ends a run that took hours.
- **Quietly drop it.** Now your data has a hole nobody knows about, and you will
  never find out why the totals look odd.

So there is a third door. Bad records are moved to a **quarantine** table, along
with a note saying which rule they broke and a complete copy of the original
record. The good records carry on. Nothing is lost, nothing is hidden, and
anyone can go and look at what failed.

Quarantine is a waiting room, not a bin. If it starts filling up, that is a
signal: either the source changed shape, or one of our rules is wrong.

## One folder per day, and redoing a day safely

Everything is filed by date: one folder per day, per source. `2024-05-10` lives
in its own place.

This makes the most common operation safe. If one day's data was fetched during
an outage and came back incomplete, you fetch that day again. The new version
**replaces** that day's folder, and no other day is touched. You do not have to
rebuild six years to fix one Tuesday.

## "Exactly the same, twice" — harder than it sounds

One requirement was that fetching the same day twice should produce a file that
is identical **down to the last byte**. Not "the same information" — literally
the same bytes.

That matters for a scientific claim. If someone repeats your work and gets a
file that differs, they have to work out whether the difference is meaningful.
Identical bytes means there is nothing to investigate.

But there was a conflict in the plan. It also said every record should be
stamped with the time it arrived. Those cannot both be true: **stamp the page
with the time and no two copies can ever match**, because the time is different.

Think of photocopying. Two copies of a page are identical — unless the page has
"printed at 14:32:07" on it, in which case they never are.

The fix is to take the timestamp off the page. Records carry no arrival time.
Instead there is a separate **logbook** recording every load: what was fetched,
when, how many records, how many quarantined. Provenance is fully kept. It just
is not written on the thing that has to be reproducible.

Two other things had to be handled. The loading tool likes to stamp each row
with a random ID and a load number derived from the clock — both had to be
switched off. And the records had to be sorted into a fixed order first, because
the website does not promise to send them in the same order twice, and a
different order is different bytes.

## Twelve hours, or half an hour

The first attempt at fetching a whole year was going to take about **twelve
hours**. Not because of the speed limit, and not because the data was huge —
the fetching itself was only 25 seconds out of every five minutes.

The cost was in the **filing**.

Remember that everything is filed one folder per day. It turns out that putting
data into a folder has a fixed cost of about a second and a half — and it barely
matters how much you put in. Measured with the same 18,000 records:

| Filing 18,000 records into | Time |
| --- | --- |
| 1 folder | 7 seconds |
| 9 folders | 17 seconds |
| 90 folders | 139 seconds |

Twenty times slower for exactly the same records. The cost is the *number of
folders touched*, not the amount of data.

Now, the satellites are fetched in groups of 200 at a time — about 37 groups to
cover them all. The original code filed each group away as soon as it arrived.
Which meant every single day-folder was opened, updated and closed **37 times
over**: around 13,500 filing operations where 366 would do.

The fix is to wait. Collect all 37 groups for a stretch of time first, put them
together, and *then* file — so each day-folder is touched exactly once.

There is now a test that specifically checks this, because it is the kind of
mistake that leaves no trace. The data would be perfectly correct either way.
It would just take twelve hours instead of half an hour, and nobody reading the
code would see anything wrong.

## The mistake I made, and how it was caught

This is worth telling because the *code* was right and the *check* was wrong,
which is the more dangerous of the two.

The storage format keeps a **catalogue** of which files currently belong to the
table. When a day is replaced, the old file is **not deleted** — it is simply
removed from the catalogue and left sitting on the shelf until a future
tidy-up.

I checked byte-identity by looking at the **shelf**: listing every file in the
day's folder and comparing them. That is the wrong shelf. It includes files the
table no longer uses.

It surfaced when a test replaced a day with corrected values and then found
*both* the old and new values present. The table itself was perfectly correct —
ask it properly and it returns only the new values — but my check was reading
withdrawn books.

Worse, it had already produced a confident-looking result: "14 out of 14 days
byte-identical." That number was meaningless, because comparing stale files to
stale files passes no matter what. The check now asks the catalogue, never the
shelf.

Two smaller things also bit, both specific to Windows: the storage format writes
file addresses in a slightly malformed way that loses the drive letter, and the
loading tool generates filenames long enough to hit Windows' limit on how long a
file path may be.

## About the test data

Tests must run without the internet, so small saved copies of real responses
live in the repository.

For **NASA**, those are genuine and unmodified — the data is public domain. The
chosen sample is 10–11 May 2024, the strongest geomagnetic storm in two decades,
where the disturbance index reaches −406 and the Kp scale hits its maximum of
9.0. Real extreme values, so the safety checks are tested against the worst
thing that actually happened rather than something imagined.

For **Space-Track** it is different. That data comes with a US-government
agreement that does not allow handing out copies. Putting real records in a
public repository would be doing exactly that.

So those files keep the real *shape* and substitute the *values*: every field
name, the fact that numbers arrive as text, how blanks are marked, the exact
column layout of the orbit lines — all genuine. The satellite IDs, names and
orbital values are not. They test the parts that need testing (reading,
converting, checking) and never reach a scientific result. One record is
deliberately impossible, so the quarantine path is tested on something that must
fail.

## What later turned out to be wrong

Two things in this document did not survive contact with the full dataset, and
it is more useful to say so than to quietly edit them away.

**The "exactly the same, twice" promise was too strong.** The method used to
guarantee it — replacing a day's file outright — turned out to be about **two
hundred times slower** than simply adding a new file. On real volumes it never
finished: a whole year ran for two hours and completed nothing.

So the filing rule changed to the simpler one the plan originally called for:
**always add, never replace**, and remove the duplicates later when the data is
cleaned. Fetching the same day twice now leaves two copies, and the cleaning
step collapses them into one.

What survives is still worth having: the *contents* of a file are identical for
identical data. What is lost is that a day's folder no longer holds exactly one
file. The thing you can reproduce exactly is the cleaned table, not the raw
folder.

**The timing explanation was wrong too.** Filing did not cost a second and a
half per folder — *replacing* did. Simply adding files does 90 folders in six
seconds. With that fixed, the first quarter of a year landed 1,323,708 records
in about ten minutes.

## What was checked

| Check | Result |
| --- | --- |
| Speed limiter, 700 simulated requests | never exceeded either limit |
| Catalogue records validated | 12,865 of 12,865 clean |
| A year of space weather | 8,784 records = 366 days × 24 hours |
| Fetching a day twice | identical, byte for byte |
| Other days after a re-fetch | untouched |
| Broken records | quarantined with reason and full copy |
| Whole test suite | 63 passed, no internet |

## What is still open

**The speed limiter only counts its own process.** Two copies running at once
would each think they were within the limit while together exceeding it. Phase 3
must not run Space-Track fetches in parallel without fixing this first.

**Old files are never cleaned up.** Every re-fetch leaves the superseded version
on the shelf. Nothing removes them yet, so repeated backfills grow storage
forever. That needs a tidy-up policy in Phase 4.

**The reading tool needs a workaround.** The data is stored in one format
(Iceberg) and Phase 2 will read it with a different tool (DuckDB). That pairing
was tested rather than assumed, and the direct route does not work here —
DuckDB cannot open the file addresses Iceberg writes on Windows, the same
malformed-address problem described above.

There is a working route: ask the catalogue for the current list of files, then
hand that list to the reading tool. It was tested end to end and gives the right
answers — for 11 May 2024 it returns a disturbance index of −406 and Kp at its
maximum of 9.0, which is exactly the storm in the test data.

It is better than the direct route anyway, because going through the catalogue
automatically excludes the withdrawn files. But it means Phase 2 has a little
plumbing to do first, and now knows that in advance rather than discovering it
halfway through.

## What happens next

Phase 2: cleaning and combining. Removing duplicates, labelling which satellite
is which generation, and — finally — turning orbits into a measurement of how
fast each satellite is falling.
