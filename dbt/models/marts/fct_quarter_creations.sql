{#- Deals created inside a quarter. They are reported here and are not part of any snapshot. -#}
with snapshots as (
    select * from {{ ref('dim_snapshot_dates') }}
),

created as (
    select s.quarter_index, s.fiscal_quarter, s.quarter_end_ts, o.opportunity_id
    from snapshots as s
    inner join {{ ref('stg_opportunities') }} as o
        on o.created_at > s.snapshot_ts and o.created_at <= s.quarter_end_ts
),

at_end as (
    select
        c.quarter_index,
        c.fiscal_quarter,
        c.opportunity_id,
        st.value as end_stage,
        cast(am.value as decimal(14, 2)) as end_amount
    from created as c
    inner join {{ ref('int_opportunity_intervals') }} as st
        on st.opportunity_id = c.opportunity_id and st.field = 'stage'
        and st.valid_from <= c.quarter_end_ts and (st.valid_to is null or c.quarter_end_ts < st.valid_to)
    inner join {{ ref('int_opportunity_intervals') }} as am
        on am.opportunity_id = c.opportunity_id and am.field = 'amount'
        and am.valid_from <= c.quarter_end_ts and (am.valid_to is null or c.quarter_end_ts < am.valid_to)
)

select
    quarter_index,
    fiscal_quarter,
    count(*) as n_created,
    count(*) filter (where end_stage = 'Closed Won') as n_won_in_quarter,
    count(*) filter (where end_stage = 'Closed Lost') as n_lost_in_quarter,
    count(*) filter (where end_stage not in ('Closed Won', 'Closed Lost')) as n_open_at_quarter_end,
    coalesce(sum(end_amount) filter (where end_stage = 'Closed Won'), 0) as won_amount
from at_end
group by quarter_index, fiscal_quarter
