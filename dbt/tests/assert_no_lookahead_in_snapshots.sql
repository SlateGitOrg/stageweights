-- No snapshot row may use a change dated after its snapshot. Checked twice: through the change
-- timestamp the row carries, and independently by re-reading the stage straight from the change
-- log as of the snapshot instant. Any row returned is a violation.
select snapshot_row_id, 'uses a later change' as problem
from {{ ref('fct_pipeline_snapshots') }}
where last_change_at > snapshot_ts

union all

select s.snapshot_row_id, 'stage differs from the change log as of the snapshot'
from {{ ref('fct_pipeline_snapshots') }} as s
inner join {{ ref('stg_opportunities') }} as o on o.opportunity_id = s.opportunity_id
where s.stage <> coalesce(
    (
        select arg_max(h.new_value, h.changed_at)
        from {{ ref('stg_field_history') }} as h
        where h.opportunity_id = s.opportunity_id
            and h.field = 'stage'
            and h.changed_at <= s.snapshot_ts
    ),
    o.created_stage
)
