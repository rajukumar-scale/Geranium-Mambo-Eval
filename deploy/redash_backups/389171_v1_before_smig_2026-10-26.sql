-- Eval portal admin page: the LiteLLM key's spend per UTC day, last 14 days (day = epoch seconds of 00:00 UTC).
with k as (
  select token from LITELLMPUBLIC."public"."LiteLLM_VerificationToken"
  where key_alias = 'pub-atlas-commercial-sandbox' and _snowflake_deleted = false
)
select date_part(epoch_second, date_trunc('day', l.starttime)) as day, round(sum(l.spend), 2) as spend, count(*) as requests
from LITELLMPUBLIC."public"."LiteLLM_SpendLogs" l join k on l.api_key = k.token
where l.starttime >= dateadd('day', -13, date_trunc('day', sysdate())) and l._snowflake_deleted = false
group by 1 order by 1
