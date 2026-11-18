# Roadmap

## Priority: Blender-style panel editing

**Active plan:** [Panel editing phases P0–P5](PANEL_EDIT_ROADMAP.md) · [tracking Issue #5](https://github.com/mibesamiti-png/Manga-Peta-Editor/issues/5)

The new editing design covers G-based panel/vertex/edge movement, axis and signed numeric entry, X deletion with modal conflict protection, Shift+A empty panel creation, duplication, preview-based Ctrl+R quad splitting, and general polygon E-midpoint/two-vertex cutting.

**Status:** roadmap registered; phase implementation/acceptance must be tracked in Issue #5 and tested PRs. Current release remains v1.8.4-debug until verified.

## Current baseline: v1.8.4-debug

### Stabilization
- free-corner editing
- snapping / multi-handle movement
- N split / advanced split
- normal Undo / Redo
- Effect-only Undo / Redo
- project save / recovery / updater
- small-screen UI

## Next candidates

### Effect templates
- speech bubble image effects
- focus lines / speed lines
- smartphone screen template
- camera screen template
- TV / monitor template
- insert-image regions
- perspective / four-corner placement

### Animated manga
- GIF input
- APNG
- Animated WebP
- animated Effect assets
- sparkle / hearts / noise / REC blink
- timeline / playback preview
- GIF / MP4 / frame sequence export

### Productivity
- history panel
- layer locking / grouping
- dialogue list
- text-style presets
- linked panel boundaries
- pre-export validation

## Architecture direction

Current implementation is mainly `app.py`.

Future gradual extraction candidate:

```text
editor/
  items/
  dialogs/
  effects/
  project/
  export/
  ui/
```

Do not stop feature development for a single huge rewrite; extract areas incrementally.
