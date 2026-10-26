-- Eval portal admin page: live budget of the LiteLLM key the portal uses (all times as epoch seconds, UTC).
WITH k AS (
  SELECT key_alias, token, max_budget, spend, budget_duration, budget_reset_at, blocked, expires, _snowflake_updated_at
  FROM LITELLMPUBLIC."public"."LiteLLM_VerificationToken"
  WHERE key_alias = 'pub-atlas-commercial-sandbox' AND _snowflake_deleted = FALSE
),
s AS (
  SELECT SUM(IFF(l.starttime >= DATEADD('hour', -1, SYSDATE()), l.spend, 0)) AS spend_1h,
         SUM(IFF(l.starttime >= DATEADD('hour', -3, SYSDATE()), l.spend, 0)) AS spend_3h,
         SUM(l.spend) AS spend_24h,
         DATE_PART(epoch_second, MAX(l.starttime)) AS last_request_at,
         DATE_PART(epoch_second, MAX(l._snowflake_updated_at)) AS logs_synced_at
  FROM LITELLMPUBLIC."public"."LiteLLM_SpendLogs" l JOIN k ON l.api_key = k.token
  WHERE l.starttime >= DATEADD('hour', -24, SYSDATE()) AND l._snowflake_deleted = FALSE
)
SELECT k.key_alias, ROUND(k.max_budget, 2) AS max_budget, ROUND(k.spend, 2) AS spend, k.budget_duration,
       DATE_PART(epoch_second, k.budget_reset_at) AS budget_reset_at, k.blocked, DATE_PART(epoch_second, k.expires) AS expires,
       DATE_PART(epoch_second, k._snowflake_updated_at) AS key_synced_at,
       ROUND(s.spend_1h, 2) AS spend_1h, ROUND(s.spend_3h, 2) AS spend_3h, ROUND(s.spend_24h, 2) AS spend_24h,
       s.last_request_at, s.logs_synced_at, DATE_PART(epoch_second, SYSDATE()) AS checked_at
FROM k CROSS JOIN s
