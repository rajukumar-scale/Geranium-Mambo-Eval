-- Eval portal cost page v2 (2026-10-26: + HG4 runs that lost the project tag): submitted tasks in the range with their attributable task-run spend (60-day lookback, any key).
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
l as (
  select l.spend, l.starttime, to_varchar(l.request_tags) as tags,
         regexp_substr(tags, 'taskId:([^"]+)', 1, 1, 'e') as tag_task,
         regexp_substr(tags, 'contributorId:([0-9a-f]+)', 1, 1, 'e') as tag_cb,
         regexp_substr(tags, 'useCase:([^"]+)', 1, 1, 'e') as use_case
  from LITELLMPUBLIC."public"."LiteLLM_SpendLogs" l
  where l.starttime >= dateadd('day', -60, sysdate()) and l._snowflake_deleted = false
    and (contains(to_varchar(l.request_tags), 'projectId:6a5595596cf3031de47f0ea2')
         or (contains(to_varchar(l.request_tags), 'taskId:task-snapshot-hg4-with-gsuite-bundle-1174-')  -- runs that lost the project tag
             and not contains(to_varchar(l.request_tags), 'projectId:')))
),
c as (
  select l.spend, l.starttime, iff(l.use_case = 'chat-lite-sandbox', 'sandbox', 'runs') as kind,
         iff(l.use_case = 'chat-lite-sandbox', l.tag_task, m.task) as task,
         iff(l.use_case = 'chat-lite-sandbox', l.tag_cb, m.cb) as cb
  from l left join runmap m on m.run_id = regexp_substr(l.tag_task, '^task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+')
  where l.use_case = 'chat-lite-sandbox' or (l.tag_task like 'task-snapshot-hg4-%' and m.run_id is not null)
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
