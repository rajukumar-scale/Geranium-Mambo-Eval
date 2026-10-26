-- Eval portal cost page v2 (2026-10-26: + HG4 runs that lost the project tag): per contributor in the range: attributable task-run spend (any key), active days, tasks touched and submitted.
with runmap as (
  select f.value::string as run_id, a.task, a.attempted_by as cb
  from scale_prod.public.taskattempts a
  join scale_prod.view.cds_chat_task_attempt_responses r on r.task_attempt_id = a._id,
  lateral flatten(input => regexp_substr_all(to_varchar(r.respsonse), 'task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+')) f
  where a.project = '6a5595596cf3031de47f0ea2' and a.attempted_at >= dateadd('day', -60, sysdate())
  qualify row_number() over (partition by f.value::string order by a.attempted_at) = 1
),
l as (
  select l.spend, date_trunc('day', l.starttime) as day, to_varchar(l.request_tags) as tags,
         regexp_substr(tags, 'taskId:([^"]+)', 1, 1, 'e') as tag_task,
         regexp_substr(tags, 'contributorId:([0-9a-f]+)', 1, 1, 'e') as tag_cb,
         regexp_substr(tags, 'useCase:([^"]+)', 1, 1, 'e') as use_case
  from LITELLMPUBLIC."public"."LiteLLM_SpendLogs" l
  where l.starttime >= dateadd('day', 1 - {{ days }}, date_trunc('day', sysdate())) and l._snowflake_deleted = false
    and (contains(to_varchar(l.request_tags), 'projectId:6a5595596cf3031de47f0ea2')
         or (contains(to_varchar(l.request_tags), 'taskId:task-snapshot-hg4-with-gsuite-bundle-1174-')  -- runs that lost the project tag
             and not contains(to_varchar(l.request_tags), 'projectId:')))
),
c as (
  select l.spend, l.day, iff(l.use_case = 'chat-lite-sandbox', l.tag_task, m.task) as task,
         iff(l.use_case = 'chat-lite-sandbox', l.tag_cb, m.cb) as cb, iff(l.use_case = 'chat-lite-sandbox', 'sandbox', 'runs') as kind
  from l left join runmap m on m.run_id = regexp_substr(l.tag_task, '^task-snapshot-hg4-with-gsuite-bundle-1174-[0-9a-f]+')
  where l.use_case = 'chat-lite-sandbox' or (l.tag_task like 'task-snapshot-hg4-%' and m.run_id is not null)
),
spend as (
  select cb, sum(spend) as spend, sum(iff(kind = 'runs', spend, 0)) as agent_runs, sum(iff(kind = 'sandbox', spend, 0)) as sandbox,
         count(distinct day) as active_days, count(distinct task) as tasks_touched, count(*) as requests
  from c where cb is not null group by 1
),
subs as (
  select a.attempted_by as cb, count(distinct a.task) as tasks_submitted
  from scale_prod.public.taskattempts a
  join scale_prod.public.pipelinev3humannodes n on n.attempt = a._id
  where a.project = '6a5595596cf3031de47f0ea2' and n.review_level in (-1, 0)
    and a.attempted_at >= dateadd('day', 1 - {{ days }}, date_trunc('day', sysdate()))
  group by 1
)
select coalesce(s.cb, b.cb) as user_id, regexp_replace(lower(u.email), '[+]outlier$', '') as email,
       round(coalesce(s.spend, 0), 2) as task_run_spend, round(coalesce(s.agent_runs, 0), 2) as agent_runs, round(coalesce(s.sandbox, 0), 2) as sandbox,
       coalesce(s.active_days, 0) as active_days, coalesce(s.tasks_touched, 0) as tasks_touched,
       coalesce(s.requests, 0) as requests, coalesce(b.tasks_submitted, 0) as tasks_submitted
from spend s full outer join subs b on b.cb = s.cb
left join scale_prod.public.users u on u._id = coalesce(s.cb, b.cb)
where lower(coalesce(u.email, '')) not like '%@scale.com%'
order by task_run_spend desc
