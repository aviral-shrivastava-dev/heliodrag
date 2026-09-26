-- dim_satellite holds exactly one row per satellite in the newest catalogue
-- snapshot.
--
-- Not redundant with its row-count test, which passes anywhere in 10,000 to
-- 100,000. DuckDB 1.5.5 once built this table from the first of twenty Parquet
-- files only -- 865 satellites of 12,892 -- while a plain SELECT of the same
-- model returned all of them. dbt tests run as plain SELECTs, so this compares
-- the stored table against what the catalogue actually says.

with latest as (

    select count(distinct norad_id) as satellites
    from {{ ref('stg_spacetrack__satcat') }}
    where ingest_date = (select max(ingest_date) from {{ ref('stg_spacetrack__satcat') }})

),

built as (

    select count(*) as satellites
    from {{ ref('dim_satellite') }}

)

select
    latest.satellites as in_latest_catalogue,
    built.satellites  as in_dim_satellite
from latest
cross join built
where latest.satellites <> built.satellites
