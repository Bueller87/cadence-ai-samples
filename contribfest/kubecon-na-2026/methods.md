# How we choose ContribFest work

We look for useful changes to Cadence AI samples that contributors can work
on with maintainers during the session.

Start with what the participant will make happen in an application. A suitable
task has a clear purpose, a small scope and a result someone can inspect.
It should improve a recipe, test or explanation that future users will need. Larger tasks need a useful stopping point and enough context for someone
to continue afterward.

## Before adding a task

Read the relevant code and try the instructions. Include prerequisites, files
to change, validation commands and the expected result. Say whether the task
uses a real model or synthetic responses, and keep untested behavior clear.

Select only contributor tasks from the reviewed ContribFest pool. Each must
have `event:contribfest`, the event milestone and `status:ready`, with no
assignee, claim or active implementation PR. Event labels alone do not make
an organizer task suitable for attendees. Keep preparation work and PR review
in the organizer's tracking Issues, outside the shortlist. Use the repository's
[claim process](../../CONTRIBFEST.md#2-claim-one-issue) for ownership.

## Keep the list useful

Recheck availability before the session. Count the introductory retry exercise
once as a learning activity, separately from independent contributions. Agree
who will review unfinished work and how contributors can follow up.

## Design the learning experience

Give each task one useful observation, rather than a long reading list:

1. **Start with working context.** Name the agent's job, prepared files and the
   smallest command that establishes the starting behavior. Label proposed
   runners or fixtures that the contributor still needs to build.
2. **Ask for a prediction.** Point to a concrete choice: which call repeats,
   which decision applies or which result survives. Include a question only
   when its answer helps the participant make the contribution.
3. **Change one thing and inspect it.** Name the counter, receipt, state,
   history event or test failure that distinguishes the result. Preserve a
   normal successful case alongside the controlled failure.
4. **Connect it to an application.** Explain why someone would use this concept
   in their own agent, and distinguish Cadence's guarantees from application logic.

Keep task purpose separate from difficulty. A beginner may want a prepared
workflow running quickly; an experienced contributor may want to investigate
recovery boundaries or history compatibility. Coverage and tooling tasks have
their own payoff and should be described honestly.

Use a dedicated **Resources** section in each issue with two or three relevant
Cadence documentation links and a short cue for what to read. Preserve exact
technical acceptance. A comprehension check does not replace execution evidence,
human rehearsal, outsider-permission checks or final offline acceptance.

### Instructional references

These public Stanford CS107 materials inform the structure above. The task
wording and exercises here are original; the course's code and solutions are
not reproduced.

- [Assignment 0](https://web.stanford.edu/class/archive/cs/cs107/cs107.1272/assign0/):
  orient learners to starter files, run the provided program, then make a
  bounded extension and test its edges.
- [Lab 2](https://web.stanford.edu/class/archive/cs/cs107/cs107.1272/lab2/):
  state a practical learning goal, inspect a specific behavior and connect
  an experiment to a design tradeoff.
- [Debugging guide](https://web.stanford.edu/class/archive/cs/cs107/cs107.1272/resources/debugging.html):
  reproduce a small case, gather evidence, test a hypothesis and rerun the
  original case after a focused change.
