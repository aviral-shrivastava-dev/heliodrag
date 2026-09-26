-- One element set per satellite per epoch.
--
-- Two kinds of duplicate arrive here. Bronze is append-only, so re-running a
-- partition leaves a second identical copy of its rows. Separately,
-- Space-Track sometimes publishes a corrected element set for an epoch it has
-- already covered; a correction carries a NEW gp_id, so keeping the highest
-- gp_id per (norad_id, epoch) resolves both -- identical copies collapse, and a
-- correction wins over what it corrected.
--
-- Written as a GROUP BY rather than a window. DuckDB can spill an aggregation
-- to disk but not this window: on 2026-09-26, with about 30M element sets,
-- `row_number() over (partition by norad_id, epoch_at order by gp_id desc)`
-- could not fit in a 4 GB memory limit, while this form took 44 seconds -- and
-- returned the same rows, checked one for one on May 2024.
--
-- arg_max_null, not arg_max: arg_max skips rows whose value is NULL, and would
-- stitch one element set together from several.

{% set keys = ['norad_id', 'epoch_at'] %}
{% set columns = dbt_utils.get_filtered_columns_in_relation(
        ref('stg_spacetrack__gp_history'), except=keys) %}

select
    norad_id,
    epoch_at,
    {%- for column in columns %}
    arg_max_null({{ column }}, gp_id) as {{ column }}{{ "," if not loop.last }}
    {%- endfor %}

from {{ ref('stg_spacetrack__gp_history') }}
where mean_motion_rev_per_day is not null
  and epoch_at is not null
group by norad_id, epoch_at
