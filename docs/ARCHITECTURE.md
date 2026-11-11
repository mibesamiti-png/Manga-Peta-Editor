# Architecture

## Stack
- Python
- PySide6
- Pillow
- QGraphicsScene / QGraphicsView

## Main classes
- `MangaEditor` — main window / project orchestration
- `CanvasView` — canvas interaction
- `ImageItem` — normal images
- `PanelItem` — manga panels
- `EditableTextItem` — horizontal text
- `VerticalTextItem` — vertical text
- `BubbleItem` — speech bubbles

## Effects
Two broad paths:
1. Pillow-based image correction/effects
2. overlay Effect assets from the Effect folder

Effect operations also have a dedicated Undo/Redo history.

## Project format
- `.mpe.json`
- bundled assets under `<title>_assets/`
- standard project root: `Projects/`

## Current limitation
The implementation is still largely monolithic in `app.py`, so parallel work can conflict.
Keep PRs narrow and coordinate ownership in Issues.
