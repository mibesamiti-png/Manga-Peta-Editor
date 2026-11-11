# ChatGPT Multi-Agent Workflow

## Goal

Three humans use three separate ChatGPT conversations to develop Manga Peta Editor in parallel without duplicating work.

## Team model

### Team A
The repository owner and their ChatGPT.

Responsibilities:
- maintain dispatch
- watch open PRs and CI
- resolve overlap
- integrate B/C work
- take explicitly assigned implementation work

### Team B
Friend 1 and their ChatGPT.

Responsibilities:
- work only on Team B's current assignment
- create a small branch and PR
- report test evidence
- do not merge its own PR unless Team A explicitly requests it

### Team C
Friend 2 and their ChatGPT.

Same rules as Team B, but using Team C's assignment.

## Start-of-task protocol

When the human says "continue", the ChatGPT must:

1. fetch latest `main` and `develop`
2. read `AGENTS.md`
3. read `docs/TEAM_DISPATCH.md`
4. read its own Team Issue
5. inspect its own open PR and CI
6. inspect other open PRs for overlap
7. proceed with exactly one safe unfinished item

If the Team Issue has no assignment:
- do not choose a random task,
- report `IDLE`,
- wait for Team A to assign one.

## End-of-task protocol

Before reporting completion:

1. run `python -m py_compile app.py`
2. run `python scripts/check_project.py`
3. run relevant GUI checks when possible
4. push branch
5. open or update one focused PR to `develop`
6. update the Team Issue with:
   - branch
   - PR
   - commit/HEAD
   - tests
   - remaining concerns

## Integration protocol

Team A reviews:
- changed files
- overlap with other PRs
- CI
- regression risk

B/C should remain on their own task until Team A marks it DONE or assigns a new task.

## Important public-repository rule

Only synthetic/public-safe test data may be committed.
Never upload private manga pages, personal images, credentials, or unanonymized local logs.
