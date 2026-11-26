{#-
    Replays the change log into validity intervals [valid_from, valid_to) per opportunity and field
    (stage, amount, close_date, rep_probability). Each field starts from the value the opportunity
    was created with; every later change closes the previous interval via LEAD over the ordered
    changes. The open interval has valid_to = NULL. Values are text, normalised so that seeds and
    changes format identically.
-#}
with opportunities as (
    select * from {{ ref('stg_opportunities') }}
),

seeds as (
    select opportunity_id, 'stage' as field, created_stage as value,
        created_at as valid_from, 0 as source_order
    from opportunities
    union all
    select opportunity_id, 'amount', cast(created_amount as varchar), created_at, 0
    from opportunities
    union all
    select opportunity_id, 'close_date', cast(created_close_date as varchar), created_at, 0
    from opportunities
    union all
    select opportunity_id, 'rep_probability', cast(created_rep_probability_pct as varchar),
        created_at, 0
    from opportunities
),

changes as (
    select
        opportunity_id,
        field,
        case field
            when 'amount' then cast(cast(new_value as decimal(14, 2)) as varchar)
            when 'close_date' then cast(cast(new_value as date) as varchar)
            when 'rep_probability' then cast(cast(new_value as integer) as varchar)
            else new_value
        end as value,
        changed_at as valid_from,
        1 as source_order
    from {{ ref('stg_field_history') }}
),

ordered as (
    select * from seeds
    union all
    select * from changes
),

windowed as (
    select
        opportunity_id,
        field,
        value,
        valid_from,
        lead(valid_from) over w as valid_to,
        lag(value) over w as prev_value,
        row_number() over w as interval_seq
    from ordered
    window w as (
        partition by opportunity_id, field
        order by valid_from, source_order, value
    )
)

select
    w.opportunity_id,
    w.field,
    w.interval_seq,
    w.value,
    w.prev_value,
    w.valid_from,
    w.valid_to,
    -- A push is a close date moved later while the deal stays open. A late close-out, where the
    -- date is set to the day the deal actually closed, changes the stage at the same instant.
    case
        when w.field = 'close_date' and w.prev_value is not null
            then cast(w.value as date) > cast(w.prev_value as date)
                and coalesce(d.is_open, false)
        else false
    end as is_push
from windowed as w
left join windowed as s
    on w.field = 'close_date'
    and s.field = 'stage'
    and s.opportunity_id = w.opportunity_id
    and s.valid_from <= w.valid_from
    and (s.valid_to is null or w.valid_from < s.valid_to)
left join {{ ref('dim_stages') }} as d on d.stage = s.value
