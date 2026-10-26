-- Eval portal cost page v2: per UTC day, Atlas Commercial model spend on any key, plus LiteLLM logging coverage of task runs.
with runmap as (  -- each HG4 agent-env run ID, at the first submitted attempt that carries it
  select f.value::string as run_id, a.task, a.attempted_by as cb, a.attempted_at
  from scale_prod.public.taskattempts a
  join scale_prod.view.cds_chat_task_attempt_responses r on r.task_attempt_id = a._id,
  lateral flatten(input => regexp_substr_all(to_varchar(r.respsonse), 'task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+')) f
  where a.project = '6a5595596cf3031de47f0ea2' and a.attempted_at >= dateadd('day', -60, sysdate())
  qualify row_number() over (partition by f.value::string order by a.attempted_at) = 1
),
l as (
  select date_trunc('day', l.starttime)::timestamp_ntz as day, l.spend, to_varchar(l.request_tags) as tags,
         regexp_substr(tags, 'taskId:([^"]+)', 1, 1, 'e') as tag_task,
         regexp_substr(tags, 'contributorId:([0-9a-f]+)', 1, 1, 'e') as tag_cb,
         regexp_substr(tags, 'useCase:([^"]+)', 1, 1, 'e') as use_case,
         case when tag_task like 'task-snapshot-hg4-%' then 'runs' when tag_task like 'autohint-task-snapshot-hg4-%' then 'hint'
              when use_case = 'chat-lite-sandbox' then 'sandbox' else 'other' end as kind
  from LITELLMPUBLIC."public"."LiteLLM_SpendLogs" l
  where l.starttime >= dateadd('day', 1 - {{ days }}, date_trunc('day', sysdate())) and l._snowflake_deleted = false
    and contains(to_varchar(l.request_tags), 'projectId:6a5595596cf3031de47f0ea2')
),
c as (
  select l.day, l.spend, l.kind, m.run_id is not null as linked,
         iff(l.kind = 'sandbox', l.tag_task, m.task) as task, iff(l.kind = 'sandbox', l.tag_cb, m.cb) as cb
  from l left join runmap m on m.run_id = regexp_substr(l.tag_task, '^task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+')
  where l.kind != 'other'
),
spend as (
  select day, sum(iff(kind = 'runs', spend, 0)) as runs, sum(iff(kind = 'runs' and linked, spend, 0)) as runs_linked,
         sum(iff(kind = 'sandbox', spend, 0)) as sandbox, sum(iff(kind = 'hint', spend, 0)) as hint, count(*) as requests,
         count(distinct cb) as active_cbs, listagg(distinct cb, ',') as cb_ids, count(distinct task) as tasks_touched
  from c group by 1
),
logged as (
  select distinct regexp_substr(to_varchar(l.request_tags), 'taskId:(task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+)', 1, 1, 'e') as run_id
  from LITELLMPUBLIC."public"."LiteLLM_SpendLogs" l
  where l.starttime >= dateadd('day', -1 - {{ days }}, date_trunc('day', sysdate())) and l._snowflake_deleted = false
    and contains(to_varchar(l.request_tags), 'taskId:task-snapshot-hg4-with-gsuite-bundle-1174-')
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
