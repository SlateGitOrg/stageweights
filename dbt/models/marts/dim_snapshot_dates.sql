{#-
    Fiscal quarters of exactly var('quarter_days') days (13 weeks) from var('fiscal_start'), a Monday.
    A snapshot is taken at 00:00 UTC on the first day of each quarter, and only quarters that were
    complete when the extract was taken (var('data_through')) are listed, so every snapshot has a
    known in-quarter outcome.
-#}
with params as (
    select
        cast('{{ var("fiscal_start") }}' as date) as fiscal_start,
        {{ var('quarter_days') }} as quarter_days,
        cast('{{ var("data_through") }}' as date) as data_through
),

quarters as (
    select
        cast(range as integer) as quarter_index,
        fiscal_start + cast(range * quarter_days as integer) as snapshot_date,
        fiscal_start + cast((range + 1) * quarter_days as integer) as quarter_end_date,
        fiscal_start,
        data_through
    from params, range(0, 400)
)

select
    quarter_index,
    'FY' || cast(year(fiscal_start) + quarter_index // 4 as varchar)
        || ' Q' || cast(quarter_index % 4 + 1 as varchar) as fiscal_quarter,
    snapshot_date,
    cast(snapshot_date as timestamp) as snapshot_ts,
    quarter_end_date,
    cast(quarter_end_date as timestamp) as quarter_end_ts
from quarters
where quarter_end_date <= data_through
