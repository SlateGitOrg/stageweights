{#-
    Raw win-in-quarter rate of every stage x push-bucket cell, before any fallback, estimated for each
    forecast quarter from the snapshot quarters that had ended by the day the forecast quarter began
    (an expanding window). Cells with no rows are kept with n_rows = 0.
-#}
with forecast_quarters as (
    select quarter_index as forecast_quarter_index, fiscal_quarter as forecast_quarter, snapshot_date
    from {{ ref('dim_snapshot_dates') }}
),

cells as (
    select d.stage, d.stage_order, b.push_bucket
    from {{ ref('dim_stages') }} as d
    cross join (values ('0'), ('1-2'), ('3+')) as b (push_bucket)
    where d.is_open
),

grid as (
    select f.*, c.stage, c.stage_order, c.push_bucket
    from forecast_quarters as f
    cross join cells as c
),

training as (
    select
        f.forecast_quarter_index,
        s.stage,
        s.push_bucket,
        count(*) as n_rows,
        count(*) filter (where s.won_in_quarter) as n_won
    from forecast_quarters as f
    inner join {{ ref('fct_pipeline_snapshots') }} as s on s.quarter_end_date <= f.snapshot_date
    group by f.forecast_quarter_index, s.stage, s.push_bucket
)

select
    g.forecast_quarter_index,
    g.forecast_quarter,
    g.stage,
    g.stage_order,
    g.push_bucket,
    coalesce(t.n_rows, 0) as n_rows,
    coalesce(t.n_won, 0) as n_won,
    case when t.n_rows > 0 then t.n_won::double / t.n_rows end as cell_rate,
    {{ wilson_lower('coalesce(t.n_won, 0)', 'coalesce(t.n_rows, 0)') }} as wilson_lo,
    {{ wilson_upper('coalesce(t.n_won, 0)', 'coalesce(t.n_rows, 0)') }} as wilson_hi
from grid as g
left join training as t
    on t.forecast_quarter_index = g.forecast_quarter_index
    and t.stage = g.stage and t.push_bucket = g.push_bucket
