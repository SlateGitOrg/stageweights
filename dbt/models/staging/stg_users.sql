select
    user_id,
    segment,
    region
from {{ source('crm', 'users') }}
