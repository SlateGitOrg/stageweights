-- Replaying the whole change log must land on the values the opportunity table holds today.
with current_interval as (
    select opportunity_id, field, value
    from {{ ref('int_opportunity_intervals') }}
    where valid_to is null
)

select o.opportunity_id, i.field, i.value as replayed
from {{ ref('stg_opportunities') }} as o
inner join current_interval as i on i.opportunity_id = o.opportunity_id
where (i.field = 'stage' and i.value <> o.stage)
    or (i.field = 'amount' and cast(i.value as decimal(14, 2)) <> o.amount)
    or (i.field = 'close_date' and cast(i.value as date) <> o.close_date)
    or (i.field = 'rep_probability' and cast(i.value as integer) <> o.rep_probability_pct)
