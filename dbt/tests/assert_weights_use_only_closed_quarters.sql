-- The rows behind a forecast quarter's weights are exactly the snapshot rows of quarters that had
-- ended by its first day: no fewer (the window expands) and no more (no lookahead).
with expected as (
    select f.quarter_index, count(s.snapshot_row_id) as n_rows
    from {{ ref('dim_snapshot_dates') }} as f
    left join {{ ref('fct_pipeline_snapshots') }} as s on s.quarter_end_date <= f.snapshot_date
    group by f.quarter_index
),

used as (
    select forecast_quarter_index, sum(n_rows) as n_rows
    from {{ ref('fct_stage_cell_rates') }}
    group by forecast_quarter_index
)

select e.quarter_index, e.n_rows as expected_rows, u.n_rows as used_rows
from expected as e
inner join used as u on u.forecast_quarter_index = e.quarter_index
where e.n_rows <> u.n_rows
