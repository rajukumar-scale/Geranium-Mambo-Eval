Backups of the cost-page Redash queries before the 2026-10-26 change. The change was: count HG4 runs that have no project tag, and count `aect-` runs in the daily totals.

To restore a query, paste its .sql file back into Redash, or update the query through the MCP tool.
- 390879: cost per day
- 390881: cost per submitted task
- 390882: cost per contributor

390881 and 390882 change in one place only, the filter in the `l` CTE. Before, it was:
    and contains(to_varchar(l.request_tags), 'projectId:6a5595596cf3031de47f0ea2')
After, it is:
    and (contains(to_varchar(l.request_tags), 'projectId:6a5595596cf3031de47f0ea2')
         or (contains(to_varchar(l.request_tags), 'taskId:task-snapshot-hg4-with-gsuite-bundle-1174-')
             and not contains(to_varchar(l.request_tags), 'projectId:')))
390879 gets the same filter change, plus `when tag_task like 'aect-%' then 'runs'` in the kind CASE.

2026-10-26 (later): SMIG switch. Most calls now go through SMIG, not LiteLLM, so every cost query also reads
SCALE_PROD.SMIG_PROD_DP_POSTGRES.USAGE_EVENTS. The *_before_smig_* files are the versions just before that change
(390879 v2, 390881 v2, 390882 v2, 388350 v1, 389171 v1). New query 394171 = SMIG cost per portal run (runId tag).
