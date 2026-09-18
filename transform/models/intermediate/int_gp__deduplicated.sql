-- One element set per satellite per epoch.
--
-- Two kinds of duplicate arrive here. Bronze is append-only, so re-running a
-- partition leaves a second identical copy of its rows. Separately,
-- Space-Track sometimes publishes a corrected element set for an epoch it has
-- already covered; a correction carries a NEW gp_id, so keeping the highest
-- gp_id per (norad_id, epoch) resolves both -- identical copies collapse, and a
-- correction wins over what it corrected.

with ranked as (

    select
        *,
        row_number() over (
            partition by norad_id, epoch_at
            order by gp_id desc
        ) as recency_rank

    from {{ ref('stg_spacetrack__gp_history') }}
    where mean_motion_rev_per_day is not null
      and epoch_at is not null

)

select
    {{ dbt_utils.star(ref('stg_spacetrack__gp_history')) }}
from ranked
where recency_rank = 1
