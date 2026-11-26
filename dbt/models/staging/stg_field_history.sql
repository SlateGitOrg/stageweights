{#- Exact duplicate export rows are removed on the raw text, before any cast; timestamps become UTC. -#}
with deduplicated as (
    select distinct
        opportunity_id,
        field,
        old_value,
        new_value,
        changed_at
    from {{ source('crm', 'opportunity_field_history') }}
)

select
    opportunity_id,
    field,
    old_value,
    new_value,
    timezone('UTC', cast(changed_at as timestamptz)) as changed_at
from deduplicated
