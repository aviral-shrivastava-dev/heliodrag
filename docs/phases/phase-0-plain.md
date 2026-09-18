# Phase 0 — Scaffold, in plain language

The technical version is [phase-0.md](phase-0.md). This one assumes you know
nothing about programming tools. Nothing here is simplified into being wrong.

If you have not read [what this project is](overview-plain.md), start there.

## Phase 0 does none of the actual work

No satellite data is fetched in this phase. Nothing is calculated. If you ran
the project right now it would do nothing at all.

That is on purpose, and here is why.

Imagine you are opening a restaurant. You do not start by cooking. You start by
writing down the exact ingredients you will buy and from where, labelling every
shelf so nobody has to guess where things go, hiring an inspector who checks
every dish before it leaves the kitchen, putting a lock on the cupboard with the
knives, and writing down *why* you chose a gas stove over electric — so that
when someone asks in two years, you are not guessing.

Then you cook.

Phase 0 is all of that. It is the part where the workshop gets built, so that
everything made afterwards can be trusted.

## The exact shopping list

A program is built from other people's programs. This one uses 186 of them.

If you just write down "we need a calculator", you might get a different
calculator next year, and the answers might come out slightly different. For a
science project, that is a disaster: someone must be able to check your result
in five years and get the *same* number.

So there are two files:

- **`pyproject.toml`** is the shopping list: "we need these things."
- **`uv.lock`** is the receipt: "we bought exactly these 186 items, these exact
  versions, on this day."

Anyone, anywhere, can hand that receipt to the tool and get a byte-for-byte
identical set of ingredients. Even the version of Python itself is on the
receipt, rather than whatever happens to be on your computer.

The tool that does this is called **uv**. It is fast, which matters because the
inspector robot re-installs everything from scratch on every single change.

There was a real risk here worth naming. Three of the big ingredients —
**Dagster**, **dbt** and **dlt** — each demand their own versions of smaller
shared ingredients, and they have historically argued. Finding out they cannot
live together in Phase 0 costs an afternoon. Finding out in Phase 3, after
building on top of them, costs a rewrite. They resolved cleanly on the first
attempt.

## A shelf for everything

Every folder has one job, decided before anything went in it:

| Folder | What is allowed in it |
| --- | --- |
| `science/` | Pure maths. Given the same numbers in, always the same numbers out. **Never** talks to the internet or reads a file. |
| `clients/` | The **only** place allowed to talk to the internet. |
| `schemas/` | Descriptions of what the incoming data should look like, so we notice when it doesn't. |
| `defs/` | Instructions for the scheduler about what to run when — and no actual thinking. |
| `transform/` | The SQL that cleans and combines data. |
| `analysis/` | The research: statistics, charts, notebooks. |
| `tests/` | Proof that the rest works. |
| `data/` | The actual data. Never shared, never uploaded. |

Two of these rules are stricter than they look.

**`science/` never touches the internet.** That means you can test the orbital
maths a thousand times a second, offline, against answers you already know. If
maths and internet were mixed in one place, you could never be sure whether a
wrong answer came from bad maths or a bad connection.

**`analysis/` may use `src/`, but `src/` may never use `analysis/`.** The
pipeline must never depend on the research. That way, changing a chart cannot
break the data.

## Four inspectors

Every time the code changes, four different checks run. Each catches a different
kind of mistake.

**ruff — the spell-checker.** Catches words that aren't words, things written
but never used, and sloppy formatting. It also fixes most of it automatically,
so nobody argues about layout.

**mypy — the label checker.** In this project, every container has a label
saying what goes inside: "this holds a date", "this holds a number". mypy
reads every label and checks nothing is ever put in the wrong box — *before the
program is ever run*. It is set to **strict**, meaning every box must be
labelled; an unlabelled one is itself an error.

**pytest — the taste test.** Actually runs the code and checks the answers. Ten
of these exist so far.

**pre-commit — the doorman.** Stands at the door and searches your bag before
anything leaves. It checks for huge files, broken files, messy line endings —
and most importantly, it checks you are not carrying a **password** out of the
building. It also flatly refuses to let anything from the `data/` folder out the
door, for reasons in the next section.

Then there is a fifth, which is really the other four again.

**The robot inspector (CI).** On a completely fresh, empty computer somewhere
else, all four checks run again from nothing. This exists to kill the single
most common excuse in software: *"but it works on my computer."* That other
computer has none of your settings, none of your files and none of your
passwords. If it works there, it genuinely works.

## Keeping the password secret

The satellite data needs a login. So there is a password, and passwords leak in
boring ways — someone prints one while debugging, and it ends up in a log file
forever.

Three defences:

1. The password lives in a file called `.env` — a sticky note on your own desk
   that never leaves the room. Git is explicitly told to ignore it.
2. A second file, `.env.example`, *is* shared, but it lists only the *names* of
   what you need, with every value blank. It is a form, not a filled-in form.
3. Inside the program, the password travels in a **sealed envelope**. If any
   part of the code tries to print it, what comes out is `**********`.

That third one is not taken on trust. There is a test that deliberately tries to
print the password and fails if the real value appears. When the settings were
printed during this phase, the real account name appeared and the password came
out as ten stars — exactly as intended.

## A speed limit built into the blueprints

Space-Track's rule is: fewer than 30 requests a minute, fewer than 300 an hour.
Break it and your account is cut off — which, in the middle of a six-year data
collection, is a genuinely bad day.

Most projects write that number in a comment and hope everyone reads it.

Here, the limit is part of the settings, with a hard ceiling: **you can dial it
down, but you cannot dial it up.** If someone sets it to 60, the program refuses
to start and says why. A refusal to start is a small annoyance; a blocked
account is a lost week.

There is a test that tries to set it to 60 and checks the program objects.

## Writing down why

Whenever a real choice was made, a short note goes in `docs/adr/`. It is dated,
numbered, and never edited afterwards — if the decision changes later, you write
a *new* note saying so.

The reason is simple: in a year, you will look at some choice and think "why on
earth did I do that?" — and by then you will have forgotten. The note answers.

Two notes exist so far. One explains the shopping-list tooling. The other
explains choosing **Dagster** instead of **Airflow** to run the pipeline, and it
does something slightly unusual: it writes down the strongest argument *against*
the choice. Airflow appears in far more job adverts, and someone reviewing this
project might read its absence as not knowing about it, rather than as a
decision. Saying that out loud is more useful than pretending the choice was
obvious.

The short version of the difference: **Airflow thinks in chores** — do this,
then that, then that. **Dagster thinks in things that ought to exist** — "the
table for 3 March should exist and be up to date." When your main job is
rebuilding one specific day out of two thousand, thinking in *things* is much
easier than thinking in *chores*.

## Stickers, not cupboards

Data goes through three stages: untouched originals, cleaned-up versions, and
final answers. These are traditionally called bronze, silver and gold.

The obvious way to organise that is three folders. This project does not do
that. Instead every item gets a **sticker** saying `silver` or `gold`, and they
all live in folders organised by what they *do* instead.

Why: folders force one answer forever, and moving something between them breaks
everything pointing at it. Stickers let you ask "show me everything gold"
without moving anything, and something can carry more than one sticker.

## Clearing out the old workshop

This was not an empty room. An earlier version of the project was already here,
half dismantled — with two competing schedulers, which is worse than either one
alone, because nobody can tell which is in charge.

That old layout was cleared out. Nothing is lost: git keeps a photograph of
every past state, and the old files can be brought back from a commit named
`af5d5fa` at any time.

**One box was not touched.** The `data/` folder holds 2.7 gigabytes of real
satellite data already collected — 662 days of records, about 5.5 million
satellite-days, plus a 1.5 GB database. Because of the speed limit, collecting
that again would take many hours.

So it was treated as read-only. Every check that needed a database during this
phase was pointed at a scratch copy instead, and the real one was confirmed
untouched afterwards.

## What was checked, and what could not be

Everything below was actually run, not assumed:

| Check | Result |
| --- | --- |
| All 186 ingredients install together | yes, first try |
| Spell-checker | clean |
| Label checker (strict) | clean, 19 files |
| Tests | 10 passed |
| Scheduler loads | yes |
| Database tooling runs | yes |
| Doorman checks | all pass |
| Doorman blocks the `data/` folder | confirmed — it refuses |

Two things honestly could **not** be checked:

**The shortcut buttons.** There is a file listing short commands like
`make test`. The tool that reads it, `make`, is not installed on this computer.
Each button's underlying action was run by hand instead, and all worked — but
the buttons themselves are unproven. Installing `make` fixes this.

**The robot inspector has never run.** It only wakes up when the project is
connected to GitHub, and this one is not connected yet. The instructions are
written and each command in them is verified, but nobody can yet say "the robot
approved it."

## What is deliberately missing

Nothing below is forgotten. Each arrives in its own phase.

| Missing | Arrives in |
| --- | --- |
| The code that talks to Space-Track and NASA | Phase 1 |
| The orbital maths | Phase 2 |
| The SQL that cleans and combines | Phase 2 |
| The daily schedule and automatic retries | Phase 3 |
| The emergency handbook and cloud setup | Phase 4 |
| The web app you can click around | Phase 5 |
| The statistics and the charts | Phase 7 |

## Two things we are genuinely unsure about

These are written down rather than quietly guessed at.

**A rule that contradicts itself.** The plan says three things: never change the
originals; stamp each arrival with the time it arrived; and redoing a day should
produce an exactly identical result. The first two make the third impossible —
if you fetch Tuesday again, the new copy arrives stamped with a *new* time, so
it cannot be identical to the old one.

Both ideas are good; they just belong on different shelves. The assumption made
here is that the untouched layer keeps *every* arrival, and the "identical when
redone" promise applies to the cleaned layer, after duplicates are removed. This
needs confirming before Phase 1 writes the first file.

**An untested connection.** The plan stores data in one format (Iceberg) and
reads it with a different tool (DuckDB). Those two are supposed to work
together, but that connection has not been tried yet with real data. It should
be tested on a single day early in Phase 1, rather than discovered to be awkward
in Phase 2 after a lot of work depends on it.

## What happens next

Phase 1: the code that actually fetches data. The speed limiter gets built
**before** the first real request is made — not bolted on afterwards.
