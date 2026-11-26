{#- One row per opportunity: the values it was created with and the values it holds today. -#}
select
    opportunity_id,
    owner_id,
    timezone('UTC', cast(created_at as timestamptz)) as created_at,
    created_stage,
    cast(created_amount as decimal(14, 2)) as created_amount,
    cast(created_close_date as date) as created_close_date,
    cast(created_rep_probability as integer) as created_rep_probability_pct,
    stage,
    cast(amount as decimal(14, 2)) as amount,
    cast(close_date as date) as close_date,
    cast(rep_probability as integer) as rep_probability_pct
from {{ source('crm', 'opportunities') }}
