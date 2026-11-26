{#-
    Per quarter, three forecasts of closed-won amount made from the quarter-start pipeline, against
    what those same deals actually closed for (their amount on the day they won). Quarters before
    var('backtest_start_quarter') are not scored: too little closed history to learn from.
    ape = |forecast - actual| / actual; bias = (forecast - actual) / actual.
-#}
with deals as (
    select
        s.quarter_index,
        s.fiscal_quarter,
        s.amount::double as amount,
        s.won_amount::double as won_amount,
        s.crm_default_probability::double as p_default,
        s.rep_probability::double as p_rep,
        w.weight as p_learned
    from {{ ref('fct_pipeline_snapshots') }} as s
    inner join {{ ref('fct_stage_weights') }} as w
        on w.forecast_quarter_index = s.quarter_index
        and w.stage = s.stage and w.push_bucket = s.push_bucket
    where s.quarter_index >= {{ var('backtest_start_quarter') }}
),

per_quarter as (
    select
        quarter_index,
        fiscal_quarter,
        count(*) as n_deals,
        sum(amount) as pipeline_amount,
        sum(won_amount) as actual_won,
        sum(amount * p_default) as forecast_default,
        sum(amount * p_rep) as forecast_rep,
        sum(amount * p_learned) as forecast_learned
    from deals
    group by quarter_index, fiscal_quarter
)

select
    *,
    abs(forecast_default - actual_won) / actual_won as ape_default,
    abs(forecast_rep - actual_won) / actual_won as ape_rep,
    abs(forecast_learned - actual_won) / actual_won as ape_learned,
    (forecast_default - actual_won) / actual_won as bias_default,
    (forecast_rep - actual_won) / actual_won as bias_rep,
    (forecast_learned - actual_won) / actual_won as bias_learned
from per_quarter
