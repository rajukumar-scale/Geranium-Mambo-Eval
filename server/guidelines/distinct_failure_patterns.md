# CB Guideline: Distinct Failure Patterns

HarmonyGames · Updated October 8, 2026 (Spec V2)

How to build a task whose six baseline rollouts fail in different ways, not all in the same way. This is the difficulty band people miss most often after the average score.

## 1. The rule

This band is measured from your six baseline rollouts, like the others in the Long-Horizon guideline.

- A **failing rollout** is one that failed at least one rubric criterion.
- A rollout's **failure pattern** is the exact set of criteria it failed.
- Two failing rollouts are **different patterns only if they differ on at least two criteria**. Rollouts that failed the same criteria, or differ on just one, are one pattern.
- You need **at least two distinct patterns**, and **no single pattern may cover more than half** of the failing rollouts.

The more rollouts that fail, the more different their failures must be:

| Failing rollouts | Largest pattern allowed | What you need |
|---|---|---|
| 1 | none | Cannot pass. One failure is one pattern. The task is too easy |
| 2 | 1 | The two must differ on at least two criteria |
| 3 | 1 | All three different |
| 4 | 2 | At least two patterns, none covering more than two rollouts |
| 5 | 2 | At least three patterns, none covering more than two rollouts |
| 6 | 3 | At least two patterns, none covering more than three rollouts |

**Three and five are the hard cases.** With three or five failing rollouts, almost every failure has to be different. A task where five rollouts fail and four of them miss the same criterion fails this band, even with a low average score.

## 2. How to count it

1. For each failing rollout, write down the criteria it failed, for example R5, R6, R12.
2. Compare each pair. If the two lists differ by zero or one criterion, put them in the same group.
3. If a rollout is one criterion away from any rollout already in a group, it joins that group. Count conservatively: when in doubt, merge.
4. Count the groups, then check the size of the largest one against the table in section 1.

**Worked example: fails.** Four of six rollouts failed:

- Run 1: R12
- Run 2: R12, R20
- Run 3: R12
- Run 4: R12

Runs 1, 3 and 4 are identical. Run 2 is one criterion away from them. That gives one pattern covering four of four failing rollouts, so the band fails. The task was hard in exactly one place, R12, and every rollout tripped over it.

**Worked example: passes.**

- Run 1: R5, R6
- Run 2: R5, R6, R9, R10
- Run 3: R9, R10
- Run 4: R5, R6

Runs 1 and 4 are identical, which is two of four (50%) and allowed. Run 2 differs from Run 1 on two criteria (R9, R10), so it is a separate pattern. Run 3 differs from both. That gives three patterns, largest 50%, so the band passes. The rollouts failed different traps and each trap shows up on two criteria. Sections 4 and 5 explain how to build both.

## 3. Why tasks fail this band

When this band fails, the task is usually hard in only one way, or each of its traps is visible on only one criterion. These are the common causes:

| Cause | What it looks like |
|---|---|
| One trap does all the work | Every failing rollout misses the same thing, and the other traps get found six of six times. In a real case, four rollouts scored 100% and the other two both lost the same chain: one pattern, 100% share. |
| Each trap is graded on one criterion | A rollout that misses trap A and one that misses A and B differ on only one criterion, so they count as one pattern. Small differences between rollouts never add up to two criteria. |
| One early miss blocks everything else | The agent misses the one document that unlocks the rest, so every rollout that misses it fails the same ten criteria together. In one real task, a single unfound "current rules" document drove more than ten criteria. |
| The trap is just "did it open the file" | Opus 5.5 is thorough at retrieval. A trap that only tests whether the agent searches or opens a record is usually found six of six times. If it is badly hidden, it is missed six of six times. Either way it never splits the rollouts. |
| A criterion nobody can pass | A criterion that fails in every rollout is in every pattern, so it adds nothing to the difference between them. It is often a defect, not difficulty: the value is not visible to the persona, the source cannot be read, or the criterion is too specific. |

## 4. Build at least three traps that split the rollouts

A trap splits the rollouts when Opus sometimes gets it right and sometimes does not. A trap that every rollout gets right, or every rollout gets wrong, adds no variety. Aim for traps that you'd expect a careful agent to get wrong roughly a third to a half of the time.

**Trap types that split Opus rollouts:**

| Type | Example |
|---|---|
| A judgment call with a clear decider | Whether a nickname used in a Linear ticket ("win&collect") refers to the feature flag in the task. The deciding record exists, but reaching the right call still takes thought. In a real case, this split four to two. |
| A rule that changes a number | Receipts are dated in UTC, not Pacific, so a few late-evening charges move from January to February. Some rollouts apply the rule and some don't. |
| Exact counts over many records | "How many of the 133 PRs touch the hot path" when no single call returns the answer. Rollouts miss different records. In a real case, six of six failed with five different patterns. |
| A correction later in the thread | A message that sounds like the conclusion, followed by replies that overturn it. Some rollouts stop reading early. |
| A delivery detail stated early | "Reply in his thread, and cc Leonard," stated at the top of a long request. Some rollouts drop it by the end. |

- **At least three traps, four is safer.** Two traps leave the band to luck. With three or four, a different mix of misses per rollout is the normal outcome.
- **Each trap independent.** Missing one must not cause or prevent missing another. Put them on different parts of the task, in different services, testing different kinds of reasoning.
- **Each trap with its own decider.** Every trap needs its own dated record, visible to the persona, that settles the right answer. Two traps settled by the same message are one trap.

**Avoid:** traps that only test whether the agent issues one search or opens one named file, and traps whose decider is so buried that no careful agent would find it. The first gets found every time. The second gets missed every time and is likely to be judged a Unique Ground Truth failure in /prompt.

## 5. Write the rubric so each trap shows up on two criteria

This is the single most effective change. When missing a trap fails two criteria, a rollout that missed it is automatically two criteria away from one that didn't, which makes it a separate pattern.

Do this when you write the rubric, from the prompt and your Oracle Events, not after you see the rollouts. Both criteria must be atomic, must trace to the prompt, and must not be duplicates. Each one has to be something a rollout could pass while failing the other.

| Pair | Example | Why it's not a duplicate |
|---|---|---|
| Decision + reason | "The agent holds ZOM-260" and "The agent states that ZOM-260 is held because of Arthur's February 10 rule" | A rollout can hold it for the wrong reason |
| One rule, two records | A superseded rule changes the outcome for both ZOM-260 and ZOM-463, and each gets its own criterion | A rollout can apply the rule to one and miss the other |
| One rule, two figures | The UTC rule moves both the January total and the February total | A rollout can get one month right and the other wrong |
| A value + where it goes | The corrected CPI, and the correction in #executives naming who had the old figure | Separate claims on separate deliverables |

**Not a second criterion:** the same number checked in the sheet and again in the Slack post, or "posts the total" next to "posts the correct total". These fail together every time. /rubric flags them as duplicates, and they add nothing to the patterns.

## 6. Don't let one miss sink everything

If the agent misses the first trap, it should still be able to reach and solve the others. Check this in your Oracle Events. If one early step unlocks every later trap, you have one trap, not four.

If one gateway step is unavoidable, make it findable: point to it from where the agent naturally looks. Put the real difficulty after it.

Read your Opus 5.5 pre-check with this in mind. If the run failed nearly everything because of one early miss, expect the six rollouts to do the same.

## 7. Criteria every rollout fails

A criterion that fails in all six rollouts lowers the score but does not separate any of them. Before you treat it as proof the task is hard, check it the same way you would for an all-fail justification:

- Can the persona see the value, under the ACL?
- Can the tool actually read the source? For example, some injected Drive text files return an error when read.
- Is the criterion asking for more than the prompt asked?

If it is a genuine wall, you can keep it, but it does not count toward this band. You still need the independent traps from section 4 beside it. If the decider is too hard to find, seed a pointer to it in the universe. Do not hint in the prompt.

## 8. If the band fails

**Change the task, not the rubric.** The Long-Horizon rule applies here too. Do not delete criteria, merge them or split them after the fact just to change how the patterns count. Fix the task, re-run the baseline, and let the rubric grade it the same way it would have before.

| What you see | What to change |
|---|---|
| Only one rollout failed | The task is too easy. Add traps from section 4. |
| Every failing rollout missed the same thing | One trap is carrying the task. Add two or more independent traps on other parts of the task (section 4). |
| Every rollout failed a long run of criteria together | An early miss is blocking the rest. Make the gateway findable and move the difficulty after it (section 6). |
| Rollouts differ, but only by one criterion each | The traps were written as single criteria. For the next revision, design each trap so it naturally produces two independent outcomes (section 5), then re-run. |
| A criterion failed in all six rollouts | Check it for a defect first (section 7). |

## 9. What not to do

- Adding criteria the prompt never asked for, just to create differences. /rubric flags them as beyond the prompt.
- Making criteria vague so rollouts score differently. That reads as ambiguous rubrics, and the standard-deviation band catches it above 0.3.
- Adding a hint, or narrowing the prompt, to bring a wall trap down. Put the missing pointer in the universe instead.
- Revising the prompt over and over until the baseline numbers look right. The baseline is the measurement, not the target.

## 10. Before you trigger the baseline

- At least three, preferably four, traps, each with its own dated decider that the persona can see.
- Each trap is one a careful agent would get right some of the time, not always and not never.
- No trap is only "did it open the file".
- Missing one trap does not block the others.
- Each trap is graded on two atomic, non-duplicate criteria: decision + reason, two records, two figures, or a value + where it goes.
- Any criterion you expect every rollout to fail has been checked for a defect.

After the baseline, count the patterns by hand using section 2 before you conclude the band passed. Over-counting is easy.

## 11. The whole thing in seven lines

1. Patterns = the set of criteria each failing rollout failed.
2. Different only if they differ on 2+ criteria. One apart = same.
3. Need 2+ patterns, none over half the failing rollouts.
4. Build 3-4 independent traps Opus sometimes gets right.
5. Grade each trap on 2 atomic criteria, not 1.
6. Don't let one early miss block the rest.
7. If the band fails, change the task, not the rubric.
