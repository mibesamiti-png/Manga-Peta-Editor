# AGENTS.md

## Project
Manga Peta Editor

## Source of truth
- Stable/public baseline: `main`
- Integration branch: `develop`
- Current baseline version: `v1.8.4-debug`
- Main implementation: `app.py`

## Required workflow
1. Read this file, README, CONTRIBUTING, and relevant docs before editing.
2. Start from the latest `develop`.
3. One issue / one focused branch / one focused PR.
4. Do not mix large refactors with unrelated feature work.
5. Run:
   - `python -m py_compile app.py`
   - `python scripts/check_project.py`
6. For GUI changes, record actual Windows test steps in the PR.
7. Prefer small reversible changes.

## Privacy / public repository rules
Never commit:
- user projects
- private manga/artwork
- personal images
- tokens, passwords, API keys
- unanonymized logs
- local absolute paths containing personal information

Only use synthetic/public-safe test assets in the public repository.

## High-risk regression areas
- page switching
- Undo / Redo
- Effect-only Undo / Redo
- free-corner panel editing
- snapping / N-split
- save / reload / recovery
- updater drag-and-drop
- small-screen UI

## Current architecture
The application is still mostly monolithic in `app.py`.
Avoid a giant one-shot refactor. Extract modules gradually when a feature is already being changed.

## Branch naming
- `feature/<topic>`
- `fix/<topic>`
- `docs/<topic>`
- `test/<topic>`

## Pull request target
Normal work targets `develop`.
Release/stabilization PRs merge `develop -> main`.
