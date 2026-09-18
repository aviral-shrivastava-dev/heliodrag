-- The analysis grain: the V2 Mini natural experiment.
--
-- The three V2 Mini variants share an identical 29 m span while differing in dry
-- mass by 1.67x (530 / 700 / 910 kg), and flew concurrently for 22-32 months.
-- Comparing them holds geometry, altitude shell, epoch and space weather fixed
-- while area-to-mass varies -- which is the comparison Oliveira et al. (2025)
-- asked for and could not make with a single-cohort sample.
--
-- Exclusions, each of which would otherwise inject a non-atmospheric signal:
--
--   * orbit-raising days: thrust, not drag
--   * the first 45 days after launch: still climbing to the operational shell
--   * null decay rates: series edges and gaps longer than four days
--   * uncatalogued satellites: already excluded upstream by the inner join,
--     and measured by fct_catalogue_coverage
--
-- This model does not fit a regression -- it produces the conditioned panel that
-- one is fitted to, so the modelling choices stay visible in SQL rather than
-- buried in a notebook.

with panel as (

    select *
    from {{ ref('fct_satellite_day') }}
    where in_v2_natural_experiment
      and decay_rate_km_per_day is not null
      and not is_likely_orbit_raising
      and days_since_launch >= 45
      and f10_7_sfu is not null
      and ap_daily is not null

),

-- Decay rate depends strongly on altitude, and the variants do not sit in
-- identical shells, so an unconditioned comparison partly measures altitude.
-- Centring each observation against its own shell-and-day peer group removes the
-- shared environment and leaves the variant-specific residual.
shell_day_baseline as (

    select
        altitude_shell_km,
        observation_date,
        avg(decay_rate_km_per_day) as shell_day_mean_decay,
        count(*)                   as shell_day_satellites
    from panel
    group by 1, 2

)

select
    panel.norad_id,
    panel.observation_date,
    panel.generation,

    panel.dry_mass_kg,
    panel.span_m,
    panel.amr_proxy,

    panel.altitude_shell_km,
    panel.semimajor_axis_km,
    panel.periapsis_km,
    panel.days_since_launch,

    panel.decay_rate_km_per_day,
    baseline.shell_day_mean_decay,
    baseline.shell_day_satellites,

    -- The dependent variable: decay relative to everything else flying in the
    -- same shell on the same day. A variant effect shows up here; a solar-cycle
    -- or altitude effect does not, because it is shared by the peer group.
    panel.decay_rate_km_per_day - baseline.shell_day_mean_decay as excess_decay_km_per_day,

    panel.f10_7_sfu,
    panel.ap_daily,
    panel.kp_max,
    panel.dst_min_nt,

    -- Storm classification follows the conventional Dst thresholds, so results
    -- can be compared against the published storm-response literature.
    case
        when panel.dst_min_nt <= -250 then 'severe'
        when panel.dst_min_nt <= -100 then 'intense'
        when panel.dst_min_nt <=  -50 then 'moderate'
        when panel.dst_min_nt <=  -30 then 'weak'
        else 'quiet'
    end as storm_class

from panel
inner join shell_day_baseline as baseline
    on panel.altitude_shell_km = baseline.altitude_shell_km
   and panel.observation_date = baseline.observation_date
-- A peer group of one is just the satellite itself; its excess decay would be
-- identically zero and would dilute the sample.
where baseline.shell_day_satellites >= 3
