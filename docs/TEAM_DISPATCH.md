# TEAM DISPATCH — A / B / C

This file is the shared assignment board for the three-person ChatGPT development team.

## Team identities

| Team | Human | Role | Active assignment |
|---|---|---|---|
| A | Repository owner | Integration coordinator + implementation | See Team A Issue |
| B | Friend 1 | Parallel implementation | See Team B Issue |
| C | Friend 2 | Parallel implementation | See Team C Issue |

## Mandatory read order

Every ChatGPT must read, in this order:

1. `AGENTS.md`
2. this file
3. `docs/CHATGPT_MULTI_AGENT_WORKFLOW.md`
4. its own Team Issue
5. latest `develop` HEAD
6. its own open branch / PR / CI
7. other open PRs that may overlap

## Assignment states

- `IDLE` — no work assigned; do not invent a task
- `ASSIGNED` — task assigned but not started
- `ACTIVE` — implementation in progress
- `PR_OPEN` — PR submitted; wait for review/integration unless explicitly assigned another non-overlapping task
- `BLOCKED` — cannot proceed safely
- `DONE` — integrated or explicitly closed

## Conflict rule

A team must not edit code owned by another ACTIVE assignment unless Team A explicitly changes the dispatch.

If two assignments unexpectedly touch the same function/class/file area:
- stop before broad edits,
- document the collision in the Team Issue,
- prefer the smaller change,
- let Team A decide the integration order.

## Branch convention

- Team A: `team-a/<topic>`
- Team B: `team-b/<topic>`
- Team C: `team-c/<topic>`

All normal PRs target `develop`.

## Current assignments

The authoritative current task for each team is its dedicated GitHub Issue.
This file defines the process; Team A updates the issues as assignments rotate.

### Team A
Status: ACTIVE  
Purpose: integration, dispatch, current baseline maintenance, plus explicitly assigned implementation.

### Team B
Status: IDLE until its Team Issue contains an ASSIGNED task.

### Team C
Status: IDLE until its Team Issue contains an ASSIGNED task.
