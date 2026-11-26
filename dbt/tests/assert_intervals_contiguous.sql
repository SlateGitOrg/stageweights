-- Per opportunity and field, intervals must tile time: each valid_to is the next valid_from (no
-- overlap, no gap), exactly one interval is open, and the first starts when the deal was created.
-- Any row returned is a violation.
with ordered as (
    select
        *,
        lead(valid_from) over (partition by opportunity_id, field order by interval_seq) as next_from,
        min(valid_from) over (partition by opportunity_id, field) as first_from,
        count(*) filter (where valid_to is null) over (partition by opportunity_id, field) as n_open
    from {{ ref('int_opportunity_intervals') }}
)

select o.opportunity_id, o.field, o.interval_seq, 'overlap or gap' as problem
from ordered as o
where (o.valid_to is not null and o.valid_to <> o.next_from)
    or (o.valid_to is null and o.next_from is not null)
    or o.n_open <> 1

union all

select o.opportunity_id, o.field, o.interval_seq, 'does not start at creation'
from ordered as o
inner join {{ ref('stg_opportunities') }} as s on s.opportunity_id = o.opportunity_id
where o.interval_seq = 1 and o.valid_from <> s.created_at

union all

select s.opportunity_id, f.field, 0, 'missing field'
from {{ ref('stg_opportunities') }} as s
cross join (values ('stage'), ('amount'), ('close_date'), ('rep_probability')) as f (field)
where not exists (
    select 1 from {{ ref('int_opportunity_intervals') }} as i
    where i.opportunity_id = s.opportunity_id and i.field = f.field
)
