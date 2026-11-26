{#-
    Deal-level calibration of each method over the scored quarters: the mean probability it
    predicted, the win rate that was realised, their gap, and the Brier score.
-#}
with deals as (
    select
        s.won_in_quarter::integer::double as won,
        s.crm_default_probability::double as p_default,
        s.rep_probability::double as p_rep,
        w.weight as p_learned
    from {{ ref('fct_pipeline_snapshots') }} as s
    inner join {{ ref('fct_stage_weights') }} as w
        on w.forecast_quarter_index = s.quarter_index
        and w.stage = s.stage and w.push_bucket = s.push_bucket
    where s.quarter_index >= {{ var('backtest_start_quarter') }}
),

long_form as (
    select 'crm_default' as method, p_default as predicted, won from deals
    union all
    select 'rep_entered', p_rep, won from deals
    union all
    select 'learned', p_learned, won from deals
)

select
    method,
    count(*) as n_deals,
    avg(predicted) as mean_predicted,
    avg(won) as realised_rate,
    avg(predicted) - avg(won) as mean_gap,
    avg((predicted - won) * (predicted - won)) as brier
from long_form
group by method
