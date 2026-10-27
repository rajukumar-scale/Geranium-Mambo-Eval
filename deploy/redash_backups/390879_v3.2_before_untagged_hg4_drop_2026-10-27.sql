-- Eval portal cost page v3.2 (2026-10-26: + SMIG, aect runs tied via the pipeline node, untagged pub-mcp-commercial calls count as task runs): per UTC day, Atlas Commercial model spend from LiteLLM's log (any key) and SMIG's usage events, plus coverage of task runs.
with runmap as (  -- each HG4 agent-env run ID, at the first submitted attempt that carries it
  select f.value::string as run_id, a.task, a.attempted_by as cb, a.attempted_at
  from scale_prod.public.taskattempts a
  join scale_prod.view.cds_chat_task_attempt_responses r on r.task_attempt_id = a._id,
  lateral flatten(input => regexp_substr_all(to_varchar(r.respsonse), 'task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+')) f
  where a.project = '6a5595596cf3031de47f0ea2' and a.attempted_at >= dateadd('day', -60, sysdate())
  qualify row_number() over (partition by f.value::string order by a.attempted_at) = 1
),
nodemap as (  -- aect-<pipeline node>-... runs (launched from the task itself): node -> task and its worker
  select _id as node, task, worker as cb from scale_prod.public.pipelinev3humannodes where project = '6a5595596cf3031de47f0ea2'
),
mcp_keys as (  -- the task-run key (rotated 2026-10-20, so every token with that alias): its untagged calls are task runs too
  select token from LITELLMPUBLIC."public"."LiteLLM_VerificationToken" where key_alias = 'pub-mcp-commercial'
),
mcp_principals as (
  select distinct id from scale_prod.smig_prod_cp_postgres.smig_principals where name = 'litellm-pub-mcp-commercial'
),
lite as (  -- LiteLLM: rows tagged with the project, or HG4 runs that lost the project tag
  select l.starttime, l.spend, to_varchar(l.request_tags) as tags,
         coalesce(regexp_substr(tags, 'taskId:([^"]+)', 1, 1, 'e'),
                  iff(l.api_key in (select token from mcp_keys), 'untagged:pub-mcp-commercial', null)) as tag_task,
         regexp_substr(tags, 'contributorId:([0-9a-f]+)', 1, 1, 'e') as tag_cb,
         regexp_substr(tags, 'useCase:([^"]+)', 1, 1, 'e') as use_case
  from LITELLMPUBLIC."public"."LiteLLM_SpendLogs" l
  where l.starttime >= dateadd('day', -1 - {{ days }}, date_trunc('day', sysdate())) and l._snowflake_deleted = false
    and (contains(to_varchar(l.request_tags), 'projectId:6a5595596cf3031de47f0ea2')
         or (contains(to_varchar(l.request_tags), 'taskId:task-snapshot-hg4-with-gsuite-bundle-1174-')
             and not contains(to_varchar(l.request_tags), 'projectId:'))
         or (l.api_key in (select token from mcp_keys)
             and not contains(to_varchar(l.request_tags), 'projectId:') and not contains(to_varchar(l.request_tags), 'taskId:')))
),
sm_raw as (  -- SMIG (most traffic since ~10-16): latest version of each usage event with our tags
  select request_id, created_at, cost_usd, tags, __deleted, principal_id
  from scale_prod.smig_prod_dp_postgres.usage_events
  where request_id is not null and created_at >= dateadd('day', -2 - {{ days }}, date_trunc('day', sysdate()))
    and (tags:projectId::string = '6a5595596cf3031de47f0ea2'
         or (tags:taskId::string like 'task-snapshot-hg4-with-gsuite-bundle-1174-%' and tags:projectId is null)
         or (principal_id in (select id from mcp_principals) and tags:projectId is null and tags:taskId is null))
  qualify row_number() over (partition by request_id order by __timestamp desc nulls last, __dump_time desc nulls last, created_at desc nulls last) = 1
),
sm as (  -- mirrored LiteLLM traffic is left out so it isn't counted twice
  select convert_timezone('UTC', created_at)::timestamp_ntz as starttime, cost_usd as spend,
         coalesce(tags:taskId::string, iff(principal_id in (select id from mcp_principals), 'untagged:pub-mcp-commercial', null)) as tag_task,
         tags:contributorId::string as tag_cb, tags:useCase::string as use_case
  from sm_raw
  where not coalesce(__deleted, false) and not coalesce(try_to_boolean(tags:mirror::string), false)
),
l as (
  select date_trunc('day', starttime)::timestamp_ntz as day, spend, tag_task, tag_cb, use_case,
         case when tag_task like 'task-snapshot-hg4-%' or tag_task like 'aect-%' or tag_task = 'untagged:pub-mcp-commercial' then 'runs' when tag_task like 'autohint-task-snapshot-hg4-%' then 'hint'
              when use_case = 'chat-lite-sandbox' then 'sandbox' else 'other' end as kind
  from (select starttime, spend, tag_task, tag_cb, use_case from lite union all select starttime, spend, tag_task, tag_cb, use_case from sm)
  where starttime >= dateadd('day', 1 - {{ days }}, date_trunc('day', sysdate()))
),
c as (
  select l.day, l.spend, l.kind, m.run_id is not null or nm.node is not null as linked,
         iff(l.kind = 'sandbox', l.tag_task, coalesce(m.task, nm.task)) as task, iff(l.kind = 'sandbox', l.tag_cb, coalesce(m.cb, nm.cb)) as cb
  from l left join runmap m on m.run_id = regexp_substr(l.tag_task, '^task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+')
         left join nodemap nm on nm.node = regexp_substr(l.tag_task, '^aect-([0-9a-f]{24})', 1, 1, 'e')
  where l.kind != 'other'
),
spend as (
  select day, sum(iff(kind = 'runs', spend, 0)) as runs, sum(iff(kind = 'runs' and linked, spend, 0)) as runs_linked,
         sum(iff(kind = 'sandbox', spend, 0)) as sandbox, sum(iff(kind = 'hint', spend, 0)) as hint, count(*) as requests,
         count(distinct cb) as active_cbs, listagg(distinct cb, ',') as cb_ids, count(distinct task) as tasks_touched
  from c group by 1
),
logged as (  -- run IDs with any spend in either source
  select distinct regexp_substr(tag_task, '^task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+') as run_id
  from (select tag_task from lite union all select tag_task from sm)
  where tag_task like 'task-snapshot-hg4-with-gsuite-bundle-1174-%'
),
coverage as (
  select date_trunc('day', convert_timezone('UTC', m.attempted_at))::timestamp_ntz as day, count(*) as sub_runs, count(g.run_id) as sub_runs_logged
  from runmap m left join logged g on g.run_id = m.run_id
  where m.attempted_at >= dateadd('day', 1 - {{ days }}, date_trunc('day', sysdate()))
  group by 1
),
subs as (
  select date_trunc('day', convert_timezone('UTC', a.attempted_at))::timestamp_ntz as day, count(distinct a.task) as tasks_submitted,
         count(distinct a.attempted_by) as submitters
  from scale_prod.public.taskattempts a
  join scale_prod.public.pipelinev3humannodes n on n.attempt = a._id
  join scale_prod.public.users u on u._id = a.attempted_by
  where a.project = '6a5595596cf3031de47f0ea2' and n.review_level in (-1, 0)
    and a.attempted_at >= dateadd('day', 1 - {{ days }}, date_trunc('day', sysdate()))
    and lower(u.email) not like '%@scale.com%'
  group by 1
),
days as (select day from spend union select day from subs union select day from coverage)
select date_part(epoch_second, d.day) as day, round(coalesce(s.runs, 0), 2) as runs, round(coalesce(s.runs_linked, 0), 2) as runs_linked,
       round(coalesce(s.sandbox, 0), 2) as sandbox, round(coalesce(s.hint, 0), 2) as hint, coalesce(s.requests, 0) as requests,
       coalesce(s.active_cbs, 0) as active_cbs, coalesce(s.cb_ids, '') as cb_ids, coalesce(s.tasks_touched, 0) as tasks_touched,
       coalesce(b.tasks_submitted, 0) as tasks_submitted, coalesce(b.submitters, 0) as submitters,
       coalesce(v.sub_runs, 0) as sub_runs, coalesce(v.sub_runs_logged, 0) as sub_runs_logged
from days d left join spend s on s.day = d.day left join subs b on b.day = d.day left join coverage v on v.day = d.day
order by 1
