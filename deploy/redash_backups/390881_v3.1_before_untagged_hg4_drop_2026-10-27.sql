-- Eval portal cost page v3.1 (2026-10-26: + SMIG, aect runs tied to their task via the pipeline node): submitted tasks in the range with their attributable task-run spend (60-day lookback; LiteLLM any key + SMIG).
with runmap as (
  select f.value::string as run_id, a.task, a.attempted_by as cb
  from scale_prod.public.taskattempts a
  join scale_prod.view.cds_chat_task_attempt_responses r on r.task_attempt_id = a._id,
  lateral flatten(input => regexp_substr_all(to_varchar(r.respsonse), 'task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+')) f
  where a.project = '6a5595596cf3031de47f0ea2' and a.attempted_at >= dateadd('day', -60, sysdate())
  qualify row_number() over (partition by f.value::string order by a.attempted_at) = 1
),
subs as (
  select a.task, max(a.attempted_at) as last_submitted, count(*) as submissions,
         listagg(distinct regexp_replace(lower(u.email), '[+]outlier$', ''), ', ') as submitters,
         listagg(distinct a.attempted_by, ',') as submitter_ids
  from scale_prod.public.taskattempts a
  join scale_prod.public.pipelinev3humannodes n on n.attempt = a._id
  join scale_prod.public.users u on u._id = a.attempted_by
  where a.project = '6a5595596cf3031de47f0ea2' and n.review_level in (-1, 0)
    and a.attempted_at >= dateadd('day', 1 - {{ days }}, date_trunc('day', sysdate()))
    and lower(u.email) not like '%@scale.com%'
  group by 1
),
nodemap as (  -- aect-<pipeline node>-... runs (launched from the task itself): node -> task and its worker
  select _id as node, task, worker as cb from scale_prod.public.pipelinev3humannodes where project = '6a5595596cf3031de47f0ea2'
),
lite as (  -- LiteLLM: rows tagged with the project, or HG4 runs that lost the project tag
  select l.spend, l.starttime, to_varchar(l.request_tags) as tags,
         regexp_substr(tags, 'taskId:([^"]+)', 1, 1, 'e') as tag_task,
         regexp_substr(tags, 'contributorId:([0-9a-f]+)', 1, 1, 'e') as tag_cb,
         regexp_substr(tags, 'useCase:([^"]+)', 1, 1, 'e') as use_case
  from LITELLMPUBLIC."public"."LiteLLM_SpendLogs" l
  where l.starttime >= dateadd('day', -60, sysdate()) and l._snowflake_deleted = false
    and (contains(to_varchar(l.request_tags), 'projectId:6a5595596cf3031de47f0ea2')
         or (contains(to_varchar(l.request_tags), 'taskId:task-snapshot-hg4-with-gsuite-bundle-1174-')
             and not contains(to_varchar(l.request_tags), 'projectId:')))
),
sm_raw as (  -- SMIG (most traffic since ~10-16): latest version of each usage event with our tags
  select request_id, created_at, cost_usd, tags, __deleted
  from scale_prod.smig_prod_dp_postgres.usage_events
  where request_id is not null and created_at >= dateadd('day', -61, sysdate())
    and (tags:projectId::string = '6a5595596cf3031de47f0ea2'
         or (tags:taskId::string like 'task-snapshot-hg4-with-gsuite-bundle-1174-%' and tags:projectId is null))
  qualify row_number() over (partition by request_id order by __timestamp desc nulls last, __dump_time desc nulls last, created_at desc nulls last) = 1
),
sm as (  -- mirrored LiteLLM traffic is left out so it isn't counted twice
  select convert_timezone('UTC', created_at)::timestamp_ntz as starttime, cost_usd as spend,
         tags:taskId::string as tag_task, tags:contributorId::string as tag_cb, tags:useCase::string as use_case
  from sm_raw
  where not coalesce(__deleted, false) and not coalesce(try_to_boolean(tags:mirror::string), false)
),
l as (
  select spend, starttime, tag_task, tag_cb, use_case from lite
  union all
  select spend, starttime, tag_task, tag_cb, use_case from sm where starttime >= dateadd('day', -60, sysdate())
),
c as (
  select l.spend, l.starttime, iff(l.use_case = 'chat-lite-sandbox', 'sandbox', 'runs') as kind,
         iff(l.use_case = 'chat-lite-sandbox', l.tag_task, coalesce(m.task, nm.task)) as task,
         iff(l.use_case = 'chat-lite-sandbox', l.tag_cb, coalesce(m.cb, nm.cb)) as cb
  from l left join runmap m on m.run_id = regexp_substr(l.tag_task, '^task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+')
         left join nodemap nm on nm.node = regexp_substr(l.tag_task, '^aect-([0-9a-f]{24})', 1, 1, 'e')
  where l.use_case = 'chat-lite-sandbox' or (l.tag_task like 'task-snapshot-hg4-%' and m.run_id is not null) or nm.node is not null
),
spend as (
  select task, sum(spend) as spend, sum(iff(kind = 'runs', spend, 0)) as agent_runs, sum(iff(kind = 'sandbox', spend, 0)) as sandbox,
         count(*) as requests, count(distinct cb) as contributors, min(starttime) as first_run
  from c where task is not null group by 1
)
select s.task as task_id, date_part(epoch_second, s.last_submitted) as last_submitted, s.submissions, s.submitters, s.submitter_ids,
       round(coalesce(p.spend, 0), 2) as task_run_spend, round(coalesce(p.agent_runs, 0), 2) as agent_runs, round(coalesce(p.sandbox, 0), 2) as sandbox,
       coalesce(p.requests, 0) as requests, coalesce(p.contributors, 0) as contributors, date_part(epoch_second, p.first_run) as first_run
from subs s left join spend p on p.task = s.task
order by task_run_spend desc