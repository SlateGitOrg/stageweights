{#-
    The learned probability for each forecast quarter x stage x push bucket. A cell with at least
    var('min_cell_rows') training rows uses its own rate; a thinner cell falls back to the stage-only
    rate (all push buckets pooled); if the whole stage is thin too, the CRM default is kept and the
    row says so. Bounds are Wilson intervals for the rate that was actually used.
-#}
with cells as (
    select * from {{ ref('fct_stage_cell_rates') }}
),

stage_level as (
    select
        forecast_quarter_index,
        stage,
        sum(n_rows) as stage_n_rows,
        sum(n_won) as stage_n_won
    from cells
    group by forecast_quarter_index, stage
),

joined as (
    select
        c.*,
        s.stage_n_rows,
        s.stage_n_won,
        case when s.stage_n_rows > 0 then s.stage_n_won::double / s.stage_n_rows end as stage_rate,
        d.crm_default_probability::double as crm_default_probability,
        case
            when c.n_rows >= {{ var('min_cell_rows') }} then 'cell'
            when s.stage_n_rows >= {{ var('min_cell_rows') }} then 'stage'
            else 'crm_default'
        end as weight_source
    from cells as c
    inner join stage_level as s
        on s.forecast_quarter_index = c.forecast_quarter_index and s.stage = c.stage
    inner join {{ ref('dim_stages') }} as d on d.stage = c.stage
)

select
    forecast_quarter_index,
    forecast_quarter,
    stage,
    stage_order,
    push_bucket,
    case weight_source
        when 'cell' then cell_rate
        when 'stage' then stage_rate
        else crm_default_probability
    end as weight,
    case weight_source
        when 'cell' then wilson_lo
        when 'stage' then {{ wilson_lower('stage_n_won', 'stage_n_rows') }}
    end as weight_lo,
    case weight_source
        when 'cell' then wilson_hi
        when 'stage' then {{ wilson_upper('stage_n_won', 'stage_n_rows') }}
    end as weight_hi,
    weight_source,
    n_rows as cell_n_rows,
    stage_n_rows,
    stage_rate,
    crm_default_probability
from joined
