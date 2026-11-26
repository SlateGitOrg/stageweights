{#-
    One row per opportunity that existed at the latest snapshot date, judged from the change log up
    to that instant only. Flags:
      pushes_3plus        close date pushed three or more times
      stage_regression    moved back to an earlier open stage
      stale_60d           open, and nothing on the record changed for 60 days
      close_date_past     open, and the close date is already behind the snapshot date
      stage_jump          moved forward two or more open stages in one change
-#}
with as_of as (
    select
        quarter_index,
        snapshot_date,
        snapshot_ts
    from {{ ref('dim_snapshot_dates') }}
    order by quarter_index desc
    limit 1
),

intervals as (
    select i.*
    from {{ ref('int_opportunity_intervals') }} as i
    cross join as_of
    where i.valid_from <= as_of.snapshot_ts
),

current_values as (
    select
        opportunity_id,
        max(case when field = 'stage' then value end) as stage,
        max(case when field = 'amount' then value end) as amount,
        max(case when field = 'close_date' then value end) as close_date,
        max(valid_from) as last_change_at
    from intervals
    where valid_to is null or valid_to > (select snapshot_ts from as_of)
    group by opportunity_id
),

stage_moves as (
    select
        i.opportunity_id,
        bool_or(n.stage_order < o.stage_order) as has_regression,
        bool_or(n.stage_order - o.stage_order >= 2) as has_jump
    from intervals as i
    inner join {{ ref('dim_stages') }} as o on o.stage = i.prev_value and o.is_open
    inner join {{ ref('dim_stages') }} as n on n.stage = i.value and n.is_open
    where i.field = 'stage'
    group by i.opportunity_id
),

push_counts as (
    select opportunity_id, count(*) as push_count
    from intervals
    where is_push
    group by opportunity_id
),

assembled as (
    select
        o.opportunity_id,
        o.owner_id,
        u.segment,
        a.snapshot_date as as_of_date,
        c.stage,
        d.is_open as open_at_latest_snapshot,
        coalesce(p.push_count, 0) as push_count,
        coalesce(m.has_regression, false) as flag_stage_regression,
        coalesce(m.has_jump, false) as flag_stage_jump,
        d.is_open and date_diff('day', cast(c.last_change_at as date), a.snapshot_date) >= 60
            as flag_stale_60d,
        d.is_open and cast(c.close_date as date) < a.snapshot_date as flag_close_date_past,
        case when d.is_open then cast(c.amount as decimal(14, 2)) else cast(0 as decimal(14, 2)) end
            as pipeline_value,
        date_diff('day', cast(c.last_change_at as date), a.snapshot_date) as days_since_change
    from {{ ref('stg_opportunities') }} as o
    cross join as_of as a
    inner join {{ ref('stg_users') }} as u on u.user_id = o.owner_id
    inner join current_values as c on c.opportunity_id = o.opportunity_id
    inner join {{ ref('dim_stages') }} as d on d.stage = c.stage
    left join push_counts as p on p.opportunity_id = o.opportunity_id
    left join stage_moves as m on m.opportunity_id = o.opportunity_id
    where o.created_at <= a.snapshot_ts
)

select
    *,
    push_count >= 3 as flag_pushes_3plus,
    (push_count >= 3)::integer + flag_stage_regression::integer + flag_stale_60d::integer
        + flag_close_date_past::integer + flag_stage_jump::integer as flag_count
from assembled
