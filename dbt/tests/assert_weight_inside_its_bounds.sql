-- A learned weight that came from data lies inside its own Wilson interval, and every weight is a
-- probability.
select forecast_quarter_index, stage, push_bucket, weight, weight_lo, weight_hi
from {{ ref('fct_stage_weights') }}
where weight < 0
    or weight > 1
    or (weight_source <> 'crm_default' and (weight < weight_lo - 1e-9 or weight > weight_hi + 1e-9))
