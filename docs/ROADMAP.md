# Roadmap

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
