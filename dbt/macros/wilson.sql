{#- Wilson score interval for a binomial proportion k / n at the z in var('wilson_z') (90% two-sided). -#}

{% macro wilson_lower(k, n) -%}
case when {{ n }} > 0 then
    (
        {{ k }}::double / {{ n }}
        + {{ var('wilson_z') }} * {{ var('wilson_z') }} / (2.0 * {{ n }})
        - {{ var('wilson_z') }} * sqrt(
            ({{ k }}::double / {{ n }}) * (1 - {{ k }}::double / {{ n }}) / {{ n }}
            + {{ var('wilson_z') }} * {{ var('wilson_z') }} / (4.0 * {{ n }} * {{ n }})
        )
    ) / (1 + {{ var('wilson_z') }} * {{ var('wilson_z') }} / {{ n }})
end
{%- endmacro %}

{% macro wilson_upper(k, n) -%}
case when {{ n }} > 0 then
    (
        {{ k }}::double / {{ n }}
        + {{ var('wilson_z') }} * {{ var('wilson_z') }} / (2.0 * {{ n }})
        + {{ var('wilson_z') }} * sqrt(
            ({{ k }}::double / {{ n }}) * (1 - {{ k }}::double / {{ n }}) / {{ n }}
            + {{ var('wilson_z') }} * {{ var('wilson_z') }} / (4.0 * {{ n }} * {{ n }})
        )
    ) / (1 + {{ var('wilson_z') }} * {{ var('wilson_z') }} / {{ n }})
end
{%- endmacro %}
