# Team Workflow

```text
main
  └─ develop
       ├─ feature/...
       ├─ fix/...
       ├─ test/...
       └─ docs/...
```

## Flow

1. Create or claim an Issue.
2. Update local `develop`.
3. Create a focused branch.
4. Implement a small change.
5. Run static checks.
6. Run relevant Windows GUI tests.
7. Open a Pull Request to `develop`.
8. Merge only after review / CI.

## Commands

```bash
git switch develop
git pull
git switch -c fix/example

python -m py_compile app.py
python scripts/check_project.py
```

## Suggested ownership split

- A: panels / free corners / snapping / N split
- B: Effect / GIF / animated effects
- C: UI / layers / text
- D: project save / Undo / tests / CI

This is not fixed. Claim work in an Issue before starting to reduce conflicts.

## Release flow

Normal:
`feature|fix -> develop`

Stable/public baseline:
`develop -> main`
