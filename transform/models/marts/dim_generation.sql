-- One row per hardware generation: the thing the research question compares.
--
-- Mass and span are medians rather than means because each generation is a
-- handful of discrete build variants, so a median reports an actual
-- configuration where a mean reports one that was never built.

select
    generation,
    max(generation_confidence)                      as label_confidence,
    count(*)                                        as satellites,
    count(*) filter (where has_decayed)             as satellites_decayed,
    round(
        100.0 * count(*) filter (where has_decayed) / nullif(count(*), 0), 1
    )                                               as decayed_pct,

    median(launch_mass_kg)                          as median_launch_mass_kg,
    median(dry_mass_kg)                             as median_dry_mass_kg,
    median(span_m)                                  as median_span_m,
    median(area_to_mass_proxy)                      as median_area_to_mass_proxy,

    min(launch_date)                                as first_launch_date,
    max(launch_date)                                as last_launch_date

from {{ ref('int_satellite__generation_labeled') }}
group by generation
