{#- The six open stages and two closed ones, with the probabilities the CRM was configured with. -#}
select
    stage,
    stage_order,
    crm_default_probability,
    is_open
from (
    values
        ('Prospecting', 1, 0.10, true),
        ('Discovery', 2, 0.20, true),
        ('Solution', 3, 0.40, true),
        ('Proposal', 4, 0.60, true),
        ('Negotiation', 5, 0.80, true),
        ('Contract Sent', 6, 0.90, true),
        ('Closed Won', 7, 1.00, false),
        ('Closed Lost', 8, 0.00, false)
) as t (stage, stage_order, crm_default_probability, is_open)
