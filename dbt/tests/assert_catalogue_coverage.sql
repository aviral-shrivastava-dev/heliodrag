-- Coverage below 95% means more than normal catalogue lag is at work -- a
-- renamed bus, a schema change, or a stale generation map. Days in the trailing
-- ten weeks are exempt, because lag there is expected and unavoidable.
--
-- Returning rows fails the test.

select
    observation_date,
    satellites_observed,
    satellites_unmatched,
    coverage_pct
from {{ ref('fct_catalogue_coverage') }}
where coverage_pct < 95
  and observation_date < current_date - interval 70 day
