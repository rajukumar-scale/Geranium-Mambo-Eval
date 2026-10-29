-- Eval portal cost page: each Atlas Commercial task's platform state, for lost spend (tasks never submitted and quiet
-- for 3 days). Saved as Redash query 396595 (ds 22, tag eval-portal) = COST_TASK_STATES_QUERY_ID.
--   submitted     = any completed L-1/L0 node with an attempt, by any account (a submitted task is never lost)
--   live_claim    = a pending node held by someone whose claim hasn't run out
--   pending       = a pending node (claimed or back in the queue); paused = a paused node
--   last_activity = latest node created or claimed (epoch seconds); the portal also counts runs and reviews
--   cancel_reason = TASKS.RESPONSE:cancellation_reason; last_worker = whoever held it last
-- PUBLIC.TASKS keeps 90 days, so older tasks come back with no task_status (the portal still classifies them by node).
with n as (
  select task, status, worker, review_level::int as lvl, attempt, created_at, claimed_at, claimed_until
  from scale_prod.public.pipelinev3humannodes
  where project = '6a5595596cf3031de47f0ea2' and not coalesce(__deleted, false)
),
agg as (
  select task,
         max(iff(lvl in (-1, 0) and status = 'completed' and attempt is not null, 1, 0)) as submitted,
         max(iff(status = 'pending' and worker is not null and (claimed_until is null or claimed_until > current_timestamp()), 1, 0)) as live_claim,
         max(iff(status = 'pending', 1, 0)) as pending,
         max(iff(status = 'paused', 1, 0)) as paused,
         date_part(epoch_second, max(greatest(created_at, coalesce(claimed_at, created_at)))) as last_activity
  from n
  group by 1
),
last_worker as (
  select task, worker from n where worker is not null
  qualify row_number() over (partition by task order by coalesce(claimed_at, created_at) desc) = 1
)
select a.task, t.status as task_status, a.submitted, a.live_claim, a.pending, a.paused, a.last_activity,
       left(t.response:cancellation_reason::string, 200) as cancel_reason, w.worker as last_worker,
       regexp_replace(lower(u.email), '[+]outlier$', '') as last_worker_email
from agg a
left join scale_prod.public.tasks t on t._id = a.task
left join last_worker w on w.task = a.task
left join scale_prod.public.users u on u._id = w.worker
