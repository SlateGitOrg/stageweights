{#-
    The open pipeline as it stood at each fiscal-quarter start, rebuilt from the change log alone.
    A row is an opportunity that had been created and was in an open stage at 00:00 UTC on the
    snapshot date. Deals created during a quarter never enter that quarter's snapshot (see
    fct_quarter_creations). The outcome is read at the quarter end:
      won / lost   closed inside the quarter
      slipped      still open, and the close date held at the snapshot fell inside the quarter
      open         still open, with a close date beyond the quarter
-#}
with snapshots as (
    select * from {{ ref('dim_snapshot_dates') }}
),

opportunities as (
    select o.opportunity_id, o.owner_id, o.created_at, u.segment
    from {{ ref('stg_opportunities') }} as o
    inner join {{ ref('stg_users') }} as u on u.user_id = o.owner_id
),

intervals as (
    select * from {{ ref('int_opportunity_intervals') }}
),

existing as (
    select s.*, o.opportunity_id, o.owner_id, o.segment, o.created_at
    from snapshots as s
    inner join opportunities as o on o.created_at <= s.snapshot_ts
),

as_of_snapshot as (
    select
        e.*,
        st.value as stage,
        st.valid_from as stage_valid_from,
        cast(am.value as decimal(14, 2)) as amount,
        am.valid_from as amount_valid_from,
        cast(cd.value as date) as close_date,
        cd.valid_from as close_date_valid_from,
        cast(rp.value as integer) * 0.01 as rep_probability,
        rp.valid_from as rep_probability_valid_from
    from existing as e
    inner join intervals as st
        on st.opportunity_id = e.opportunity_id and st.field = 'stage'
        and st.valid_from <= e.snapshot_ts and (st.valid_to is null or e.snapshot_ts < st.valid_to)
    inner join intervals as am
        on am.opportunity_id = e.opportunity_id and am.field = 'amount'
        and am.valid_from <= e.snapshot_ts and (am.valid_to is null or e.snapshot_ts < am.valid_to)
    inner join intervals as cd
        on cd.opportunity_id = e.opportunity_id and cd.field = 'close_date'
        and cd.valid_from <= e.snapshot_ts and (cd.valid_to is null or e.snapshot_ts < cd.valid_to)
    inner join intervals as rp
        on rp.opportunity_id = e.opportunity_id and rp.field = 'rep_probability'
        and rp.valid_from <= e.snapshot_ts and (rp.valid_to is null or e.snapshot_ts < rp.valid_to)
),

open_pipeline as (
    select a.*, d.stage_order, d.crm_default_probability
    from as_of_snapshot as a
    inner join {{ ref('dim_stages') }} as d on d.stage = a.stage
    where d.is_open
),

pushes as (
    select s.quarter_index, i.opportunity_id, count(*) as push_count
    from snapshots as s
    inner join intervals as i on i.is_push and i.valid_from <= s.snapshot_ts
    group by s.quarter_index, i.opportunity_id
),

at_quarter_end as (
    select
        p.quarter_index,
        p.opportunity_id,
        st.value as end_stage,
        cast(am.value as decimal(14, 2)) as end_amount
    from open_pipeline as p
    inner join intervals as st
        on st.opportunity_id = p.opportunity_id and st.field = 'stage'
        and st.valid_from <= p.quarter_end_ts and (st.valid_to is null or p.quarter_end_ts < st.valid_to)
    inner join intervals as am
        on am.opportunity_id = p.opportunity_id and am.field = 'amount'
        and am.valid_from <= p.quarter_end_ts and (am.valid_to is null or p.quarter_end_ts < am.valid_to)
),

final as (
    select
        p.quarter_index || '|' || p.opportunity_id as snapshot_row_id,
        p.quarter_index,
        p.fiscal_quarter,
        p.snapshot_date,
        p.snapshot_ts,
        p.quarter_end_date,
        p.opportunity_id,
        p.owner_id,
        p.segment,
        p.stage,
        p.stage_order,
        p.amount,
        p.close_date,
        p.rep_probability,
        p.crm_default_probability,
        date_diff('day', cast(p.created_at as date), p.snapshot_date) as deal_age_days,
        date_diff('day', cast(p.stage_valid_from as date), p.snapshot_date) as days_in_stage,
        coalesce(pu.push_count, 0) as push_count,
        case
            when coalesce(pu.push_count, 0) = 0 then '0'
            when pu.push_count <= 2 then '1-2'
            else '3+'
        end as push_bucket,
        greatest(
            p.stage_valid_from, p.amount_valid_from, p.close_date_valid_from,
            p.rep_probability_valid_from
        ) as last_change_at,
        case
            when q.end_stage = 'Closed Won' then 'won'
            when q.end_stage = 'Closed Lost' then 'lost'
            when p.close_date < p.quarter_end_date then 'slipped'
            else 'open'
        end as outcome,
        q.end_stage = 'Closed Won' as won_in_quarter,
        case when q.end_stage = 'Closed Won' then q.end_amount else cast(0 as decimal(14, 2)) end
            as won_amount
    from open_pipeline as p
    left join pushes as pu
        on pu.quarter_index = p.quarter_index and pu.opportunity_id = p.opportunity_id
    inner join at_quarter_end as q
        on q.quarter_index = p.quarter_index and q.opportunity_id = p.opportunity_id
)

select * from final
