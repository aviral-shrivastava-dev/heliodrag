-- One space-weather record per hour.
--
-- Bronze is append-only, so re-running a partition leaves a second copy of its
-- rows (ADR-0005). Unlike gp_history, OMNI carries no identifier that orders
-- two copies, so any one of them is kept -- they are identical unless NASA has
-- revised the value, and a revision needs a reload of the table rather than of
-- a partition. That limitation is asserted in tests/integration/test_bronze.py.

select
    observed_at,
    epoch_date,
    f10_7_sfu,
    kp_index,
    dst_nt,
    ap_nt

from {{ ref('stg_nasa__omni') }}
qualify row_number() over (partition by observed_at order by epoch_date) = 1
