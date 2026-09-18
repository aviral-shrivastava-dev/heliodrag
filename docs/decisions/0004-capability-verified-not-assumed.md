# 4. Verify machine capability before designing around a limitation

Date: 2026-09-18 · Status: Accepted

## Context

Several early decisions were justified by claimed environment limits that had
never been tested: that Airflow could not run here, that `make` was unavailable,
and that Spark was not worth the effort.

Checking the machine directly changed three of those conclusions.

| Claim | Test | Result |
|---|---|---|
| Airflow won't run on Windows | started Docker, brought the stack up | **Wrong.** Runs fine; 16 CPUs, 8.1 GB in the VM |
| `make` unavailable | `Get-Command make` | Correct, but `choco` and `winget` are installed |
| Spark impractical | `java -version`, then a real read | Java 21 present, but Hadoop native libs are not |

## Decision

Test capability before designing around its absence. Where a limit is real,
record what was actually tested rather than the assumption.

## Verified environment

- Docker Desktop 29.7.2, Linux containers, 16 CPUs, 8.1 GB allocated
- WSL 2 present, but only the `docker-desktop` distro — no general Linux userland
- Java 21.0.8 LTS (Eclipse Temurin)
- Host: 15.7 GB RAM, 16 logical CPUs
- **C: 7.5 GB free** after pulling Airflow images; Docker stores images on C:

## Consequences

- Airflow is implemented and verified: the DAG parses, all four tasks register,
  the webserver reports healthy.
- Spark stays out for now. It fails natively with
  `UnsatisfiedLinkError: NativeIO$Windows.access0` because `winutils.exe` and
  `hadoop.dll` are absent. The common workaround is downloading those binaries
  from a third-party mirror, which is not an acceptable dependency. If Spark is
  wanted, it goes in a container like everything else.
- Disk is the binding constraint on this machine, not CPU or memory. Any further
  container work should account for C: having under 8 GB free.
