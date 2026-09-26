# Related work

Searched on 2026-09-26, to check that the research question -- do Starlink
hardware generations respond differently to the same space-weather forcing? --
is still open. It appears to be. The searches covered arXiv, AGU, Frontiers,
Springer and AIAA listings; paywalled full texts and conference proceedings
(AMOS, AIAA SciTech) were not read, so "no one has done this" means "not found
here", not proven.

## Closest work, and what each leaves open

| Work | What it does | What it does not do |
| --- | --- | --- |
| Oliveira, Zesta & Garcia-Sage (2025), [Tracking reentries of Starlink satellites during the rising phase of Solar Cycle 25](https://arxiv.org/abs/2505.13752), *Frontiers in Astronomy and Space Sciences* | 523 Starlink reentries, 2020-2024, from TLEs; superposed epochs; faster reentry and larger prediction errors with higher geomagnetic activity | Pools all satellites. Names differing areas and masses as a source of scatter in its results -- the variable this project isolates |
| Jankovic (2026), [How long can you trust a Starlink TLE?](https://arxiv.org/abs/2605.19850) | Stratifies by shell (540/550/560 km) and by generation (v1.0, v1.5, v2-mini); regresses TLE staleness on F10.7 | Measures SGP4 position error, not decay. One month (April 2026), so it cannot calibrate a generation's response to forcing; no storms, no v2-mini-opt or DTC |
| Basak, Pal & Bhattacherjee (2026), [CosmicDancePro](https://arxiv.org/abs/2604.22685) | Constellation behaviour and fleet-management strategy in two major storms, including May 2024; network effects | No comparison between hardware generations |
| Ali et al. (2026), [Starlink constellation: deployment, configuration, and dynamics](https://arxiv.org/abs/2603.25835) | Shells, manoeuvres, lifespans and failure rates, 2019-2025 | No drag or space-weather analysis by generation |
| Starlink TLE density tomography (2026, *Earth, Planets and Space*: [TLE data analysis](https://link.springer.com/article/10.1186/s40623-026-02402-1), [tomography](https://link.springer.com/article/10.1186/s40623-026-02509-5)) | Uses Starlink drag to map thermospheric density near 480 km | Fits one ballistic coefficient per satellite *because* none is known per design -- treats the generation difference as a nuisance, not a result |
| Parker & Linares (2024), [Satellite drag analysis during the May 2024 Gannon storm](https://arxiv.org/abs/2406.08617); Berger et al. (2023), [The thermosphere is a drag](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2022SW003330); Ahluwalia et al., [Loss of 12 Starlink satellites, May 2024](https://arxiv.org/abs/2410.16254) | Single-storm case studies: whole-LEO drag in May 2024, the February 2022 loss of 38 v1.5 satellites, the May 2024 losses | One event each, and no between-generation comparison |

## What this project adds

- **Generation as the variable**, not the noise: six generations, v0.9 to
  v2-mini-DTC, labelled from GCAT, including v2-mini-opt, the largest and newest.
- **The whole cycle**, 2020-01-01 to 2026-09, rising phase through maximum,
  rather than one storm or one month.
- **The same forcing for every generation**: every generation that is in orbit
  on a day sees that day's F10.7, Kp, Ap and Dst, so differences between them
  are differences in response.

## Confounders any such comparison must handle

The literature above is consistent with what the data here shows: most of an
operational Starlink's drag is cancelled by its thrusters, retired satellites
are commanded down, and newly launched ones are still raising their orbits.
The [research question](../../README.md#the-research-question) in the README
lists them; they decide which satellite-days can carry a drag signal at all.
