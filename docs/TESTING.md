# Testing

## Required static checks

```bash
python -m py_compile app.py
python scripts/check_project.py
```

## GUI testing

CI cannot fully validate PySide6 GUI behavior.
For UI changes, PRs should record:
- Windows version
- Python version
- exact operation sequence
- result
- untested areas
- traceback if any

## Regression hotspots
- page switching
- Undo / Redo
- Effect-only Undo / Redo
- free-corner editing
- snapping
- N split
- save / reload
- recovery
- updater D&D
- small-screen scroll/layout
