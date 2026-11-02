with l1 as (
  select n._ID as node, n.TASK, n.WORKER, n.STATUS, n.CLAIMED_AT, n.MAX_CLAIM, n.ATTEMPT
  from PUBLIC.PIPELINEV3HUMANNODES n join PUBLIC.TASKS t on t._ID = n.TASK
  where t.PROJECT = '6a5595596cf3031de47f0ea2' and to_varchar(n.REVIEW_LEVEL) = '-1' and n.WORKER is not null and n.CREATED_AT >= '2026-10-01'
), sub as (
  select a.TASK, a.ATTEMPTED_BY as worker, a.ATTEMPTED_AT, a.V2_TIME_SPENT_SECS
  from PUBLIC.TASKATTEMPTS a join PUBLIC.TASKS t on t._ID = a.TASK
  where t.PROJECT = '6a5595596cf3031de47f0ea2' and to_varchar(a.ATTEMPTED_AT_REVIEW_LEVEL) = '-1' and a.ATTEMPTED_AT >= '2026-10-01'
), inc as (
  select i.TASK, i.WORKER, i.PIPELINE_V3_HUMAN_NODE as node, i.CREATED_AT
  from PUBLIC.INCOMPLETETASKATTEMPTS i join PUBLIC.TASKS t on t._ID = i.TASK
  left join PUBLIC.PIPELINEV3HUMANNODES n on n._ID = i.PIPELINE_V3_HUMAN_NODE
  where t.PROJECT = '6a5595596cf3031de47f0ea2' and (n._ID is null or to_varchar(n.REVIEW_LEVEL) = '-1') and i.CREATED_AT >= '2026-10-01'
), people as (
  select worker from l1 union select worker from sub union select worker from inc
  union select trim(value)::string from table(split_to_table('{{ extra_users }}', ','))
), tr as (
  select WORKER, min(DAY) as first_day, max(DAY) as last_day from VIEW.DAILY_WORKER_TAGS where TAG_ID = '6a5f94cba4bfdd536dbced06' group by 1
), blk as (  -- mcp_ac_temp_block: offboarded people (2026-11-01)
  select WORKER, min(DAY) as first_day, max(DAY) as last_day from VIEW.DAILY_WORKER_TAGS where TAG_ID = '6a5f96f7c2e188d775ec6bcf' group by 1
)
select 'node' as kind, node, task, worker, status, date_part(epoch_second, claimed_at) as t1, date_part(epoch_second, max_claim) as t2, attempt, null as secs, null as email, null as source, null as tr_first, null as tr_last, null as blk_first, null as blk_last from l1
union all select 'submit', null, task, worker, null, date_part(epoch_second, attempted_at), null, null, v2_time_spent_secs, null, null, null, null, null, null from sub
union all select 'incomplete', node, task, worker, null, date_part(epoch_second, created_at), null, null, null, null, null, null, null, null, null from inc
union all select 'user', null, null, u._ID, null, null, null, null, null, u.EMAIL, u.WORKER_SOURCE, to_varchar(tr.first_day::date), to_varchar(tr.last_day::date), to_varchar(blk.first_day::date), to_varchar(blk.last_day::date)
  from PUBLIC.USERS u join people p on p.worker = u._ID left join tr on tr.WORKER = u._ID left join blk on blk.WORKER = u._ID
