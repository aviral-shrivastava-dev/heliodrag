# Phase 5 — Showing it, and explaining it, in plain language

The technical version is [phase-5.md](phase-5.md). This one assumes no
background. Nothing here is simplified into being wrong.

## What this phase is for

By the end of Phase 4 the project worked, but only for the person who built it.
The data sat in a database that only a programmer could question, and the
instructions assumed you already knew how everything fitted together.

This phase is about **other people**. Three things were built:

- an **explorer**: a web page, running on your own computer, where you can see
  how each kind of Starlink satellite is losing height, day by day, next to the
  space weather at the time;
- a **front page** (the README) that explains the project and gets a complete
  stranger from nothing to a working explorer;
- **published documentation** of every table the project builds.

The test for the phase was blunt: could someone who has never seen this project
get it running using only the front page? To check, a fresh copy of the project
was made -- only what anyone downloading it would get, none of the data already
collected -- and the front page's instructions were followed exactly.

## The explorer

Open it and you see four things.

**A strip of space weather along the top.** The default is a measurement called
Dst, which plunges during a geomagnetic storm -- when a burst from the Sun hits
Earth's magnetic field. Storm days are shaded grey, and the shading carries on
down through every chart below, so you can see what each kind of satellite was
doing on the same days.

**One chart per kind of satellite.** Starlink has built several generations of
satellite -- v1.0, v1.5, v2-mini and others -- that differ in size and weight.
Each chart shows the typical daily change in height for that generation: below
zero means the satellite is sinking. The line is the middle value across all
the satellites of that kind. The shaded band around it covers the middle half of
them, so you can see how spread out they are.

**A storm view.** Many storms are lined up on the day each was worst, and
averaged, so that the slow background trends cancel out and the storm's own
effect remains. Each generation is compared with how it was behaving in the five
days before, which removes things like its steering engines from the comparison.
In 2024, every generation sank faster at the peak.

**A page of warnings, called "Before you conclude".** Because a chart that looks
like an answer is not one. Working Starlinks fire small engines to hold their
height, which hides the very drag being measured; old ones are steered down on
purpose; and each generation flew at a different point in the Sun's 11-year
cycle, so their dates are tangled up with their design.

## Two things the real data taught the charts

**The raw daily line zigzags.** Each day's change in height is worked out by
comparing two measurements of the orbit. Every measurement has a small error,
and one measurement's error affects two days -- once as the "after" and once as
the "before" -- in opposite directions. So a high day tends to be followed by a
low one. For the oldest generation, the daily values were strongly
anti-correlated this way. The explorer therefore averages each day with the
three days either side by default, says so on the page, and lets you switch it
off. It averages both sides, not just the days before, so a storm is not shifted
later in time.

**A few extreme days flattened everything.** Satellites being steered down to
burn up fall hundreds of metres a day. When every chart shared one scale sized
to include those days, the ordinary satellites looked like flat lines. The
shared scale now fits the great majority of days and cuts off the rare extremes,
and the page says it has done so.

## One command, and why not the usual one

Many projects use a tool called `make` for their setup commands. The
computer this project was built on does not have it -- most Windows computers
do not -- so the old instructions would have failed on the author's own machine.
The one command is now:

```bash
uv run starlink-drag demo
```

`uv` is the one tool you install first. The command checks that you have a
Space-Track login (and if not, tells you exactly how to get one and where to put
it), installs everything else, downloads the most recent 30 days of real
satellite orbits and space weather, runs every cleaning step and every check,
and opens the explorer in your browser.

**Why you need your own login, and why no data comes with the project.** The
satellite orbits come from Space-Track, a US government service, whose rules
say you may not pass their data on. So the project cannot include any, cannot
put the explorer on the public internet, and will not invent pretend data for
you to explore. Instead, one command fetches your own copy under your own free
account. Thirty days takes a few minutes and stays well inside Space-Track's
speed limit.

## Documentation of every table, with no data in it

A tool called dbt, which builds the project's tables, can produce a website
describing every table, every column, and how each table is made from the
others. Normally it reads the real database to find out each column's type --
but the real database holds data that cannot be shared.

So the website is built from a database made **empty on purpose**: every table
exists with exactly the right columns, and not a single row. The website comes
out complete, and there is nothing in it that could not be published, because
there was nothing to put in it. A test checks that. GitHub publishes it
automatically -- once the project's owner switches on one setting.

## What building it on six years of data uncovered

**A bug in the database software itself.** When the cleaning steps ran over
several years of data for the first time, two checks failed: the table listing
every satellite had only 865 of the 12,892, and millions of daily records
pointed at satellites that seemed not to exist. Digging in showed that DuckDB,
the database program, returned the right answer when asked a question directly,
but quietly used only the first of twenty data files when asked to *save* the
same answer as a table. The question was rewritten in a way that avoids the
problem, and a new check now compares the saved table with the true count every
time. The checks did their job: nothing wrong reached the explorer.

**The speed limit was per program, not per job.** Space-Track allows fewer than
300 requests an hour. The project's limiter counted correctly within one program
-- but a big data collection runs as several programs in turn, and each new one
started counting from zero. This was noticed before any harm was done, and the
handbook now says to leave an hour between large collections.

**An empty answer came back in the wrong shape.** When Space-Track has nothing to
say, the code built an empty table with every column marked as text, instead of
numbers and dates. That is now fixed and tested.

**Space-Track published half as often in summer 2023.** For about seven weeks
in 2023 there are roughly half the usual orbit updates per satellite. This was
checked carefully, because the project's own worst failure looks similar. It is
not that failure: every satellite is still present every day, just updated less
often. It is simply how the data is.

**The biggest collection ran out of memory -- and then broke the speed limit.**
The project collects orbit data a quarter of a year at a time, holding each
quarter in memory before saving it. By 2025 there are about 12,000 Starlinks,
and a quarter no longer fitted. When that step crashed, the scheduler did what
it is set up to do: it tried again. But it tried again as a *new* program, whose
speed limiter knew nothing about the requests just made, and it started from
the beginning. About 350 requests went out in half an hour, when Space-Track's
limit is 300 an hour, and Space-Track started answering with nothing. The run
was stopped by hand. This broke one of the project's hard rules, so it is
written down plainly here rather than tidied away.

It also exposed a quieter problem: after a crash, the next save can quietly
store the *leftover* data from the crash instead of the new data, while the log
says the new data was saved. Nothing was lost this time, but it could have been.

Everything up to 26 December 2025 is safely stored, and all the tables were
rebuilt from it without contacting Space-Track: every check passed.

## How it was checked

The acceptance test, on a fresh copy of the project with none of the existing
data: it worked. Seven minutes after typing the command -- most of it spent
installing software -- the explorer was open, showing 10,738 satellites over
the most recent 30 days, built from 951,965 orbit measurements, with every one
of the 98 checks passing.

Everything else:

- The whole test suite passes -- 303 tests, up from 239 -- and never touches the
  internet.
- The explorer itself is run, invisibly, by the tests: every tab, with and
  without smoothing, and with no data at all (when it should explain how to get
  some).
- The documentation website builds in about a minute.

## What is deliberately missing

- **A public explorer.** It would share data that may not be shared.
- **The answer to the research question.** The explorer shows the data. Proper
  statistics -- with honest measures of uncertainty -- are Phase 7.

## What is still open

- Fixing the collection so that crash cannot happen again: a speed limiter
  shared between programs, collecting in smaller pieces, no automatic retry that
  asks for the same data twice, and dealing with crash leftovers. These belong
  to the collection part of the project, so they will be reviewed separately.
  Until then, data after 26 December 2025 is not collected.

- Why exactly the database program dropped the data. It has been worked around
  and guarded against, but a demonstration simple enough to send to its authors
  has not been found.
- The documentation website goes live when the owner switches on GitHub Pages.
- The demo was run end to end on Windows. The automatic checks run on Linux too,
  but they do not download live data.
