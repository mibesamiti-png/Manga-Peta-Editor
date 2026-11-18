# Blender風コマ編集 — 仕様・実装ロードマップ

Source-of-truth umbrella issue: [#5](https://github.com/mibesamiti-png/Manga-Peta-Editor/issues/5)
Baseline: `v1.8.4-debug` / Target: `develop` → QA → `main`

## Scope & principle

Keep existing image editing / text editing / free-corner operations working. Introduce Blender-like *modal* panel input gradually. Standard quad workflows remain usable while N-gon support is added later. Batch changes by behavior and test each gate before marking it complete.

### P0 — interaction foundation [NOT DONE]

- Route canvas-only edit keys through one keyboard state handler, with text editing / dialogs / input widgets excluded.
- Define `idle`, `grab`, and `split_preview` states, including selected targets and immutable starting snapshot.
- Preview is transient; commit is a single Undo transaction, cancel restores exact pre-operation geometry.
- Make page changes, mode changes, focus loss and selected-target deletion cancel active previews.
- Do not steal `X` or number keys in text fields.
- Acceptance: keystrokes cannot accidentally edit text or delete panels while a modal operation is active.

### P1 — G grab + basic panel keys [NOT DONE]

| Input | Idle action | During G |
|---|---|---|
| `G` | enter move preview for selected panel/handle(s) | no duplicate operation |
| mouse move | standard selection | preview delta from anchor |
| `X` | delete selected panels | lock movement to X axis |
| `Y` | no panel action | lock movement to Y axis |
| number | standard text/input | enter signed distance |
| `-` | standard text/input | toggle sign, whether entered before or after digits |
| Enter / left click | normal | commit |
| Esc / right click | normal | restore and cancel |
| `Shift+A` | create one empty panel | ignored while modal |
| `Shift+D` | duplicate selected panels (candidate) | ignored while modal |

**Selection target precedence:** with free-corner editing disabled, move entire panel(s). With editing enabled and handles selected, move selected vertices; an edge is represented by both adjacent selected vertices. Handle positions move by same vector; existing snap constraints may adjust it. Multi-panel selected handles retain relative positions.

**Numeric units:** page/canvas pixels (independent of viewport zoom). `G 25` means `dx=+25, dy=+25`, deliberately differs from Blender's default free-direction numeric distance. `G -25` / `G 25-` both mean `dx=dy=-25`; `G X -25` means `dx=-25,dy=0`. Moving the mouse after numeric entry should not corrupt the numeric result; mouse movement may resume only if numeric entry is cleared. `X` or `Y` after `G` is never deletion. Left-click/Enter commits once; cancellation records no history.

**Duplication:** use existing copy serialization/deserialization as the first compatibility path; copied panels retain image/effects/geometry but receive fresh independent runtime identifiers. Clarify whether follow-on G preview is wanted once basic duplication works.

### P2 — interactive Ctrl+R automatic quad split [NOT DONE]

- Start with exactly one selected quadrilateral panel.
- `Ctrl+R` enters preview; mouse motion selects horizontal/vertical cut direction.
- Preview draws dashed lines for 2–12 parts; wheel increments/decrements part count; left click commits, Esc cancels.
- Read the existing panel split gutter setting, and reuse current quad/freeform split implementation where practical.
- Preserve image appearance, transforms, and normal history; do not remove current split UI until parity checks pass.
- Out-of-range/too-small panels reject split with an explanation.

### P3 — N-gon data and E midpoint insert [NOT DONE]

- General polygon contains **3 or more** ordered points, not a fixed `len == 4`. The polygon is simple (no self-crossing), and may be concave.
- When an edge is selected, `E` inserts one vertex at the edge's midpoint. Geometry stays unchanged immediately after insertion.
- Update painting, bounding box, hit testing, handle selection, edge iteration, transform logic, snapping, image clipping, overlays, serialization, paste, Undo/Redo, and project recovery to support N vertices.
- Preserve reading old `custom_points` four-vertex projects. Never silently destroy unsupported data.
- Reject duplicate vertices, near-zero edges, and invalid self-intersecting edits.

### P4 — two-vertex polygon cut [NOT DONE]

- Two nonadjacent polygon vertices must be selected on the same panel, then `Ctrl+R`.
- Preview is their connecting segment, and its interior must lie strictly within the polygon (except endpoints). Crossing other edges or leaving a concave polygon is invalid.
- Left click commits one cut to two simple polygons; Esc cancels. Show invalid preview clearly, do not mutate.
- Use configured gutter where geometrically valid without overlap/degenerate result. Initial scope is **one chord / two panels**, not repeat automatic multicut.
- Odd-sided polygons are supported when the chord is valid. Even number of corners alone is neither necessary nor sufficient for general multi-strip splitting.

### P5 — cross-feature validation [NOT DONE]

- Old quad projects and new polygon projects: save/reload, page switch, copy, Undo/Redo, recovery.
- Effect-only undo, transforms, snapping, grouping, view zoom, text/typing focus, deletion and shortcut precedence.
- Panel contents/image/effect clipping for concave outlines, export PNG/ZIP.
- Windows screen sizes incl. 1366×768 and existing updater D&D.

## Safe implementation batches

1. **B0:** P0 + safe basic shortcuts, prove focus filtering and no modal conflict.
2. **B1:** G for whole panel plus numeric/axis + transactional Undo/cancel.
3. **B2:** selected corner/edge G via existing group-drag APIs, with snapping and validation.
4. **B3:** Ctrl+R preview for quads, gutter and wheel; tests.
5. **B4:** polygon data layer + E midpoint, legacy serialization.
6. **B5:** general two-vertex cut + validity tests and full regression.

Release after each verified batch if useful; never mark a batch complete based solely on code changes without CI/manual evidence.

## Current implementation notes

- Core: `app.py` `PanelItem`, `CanvasView`, `MangaEditor`.
- Existing methods: `add_free_panel`, `delete_selected`, `duplicate_selection`, `split_selected_panel_n`, `begin_corner_group_drag`, `update_corner_group_drag`, `end_corner_group_drag`.
- `PanelItem` currently accepts `custom_points` only when exactly 4 points. Edge loops and clamping assume 4. Do **not** merely change the count guard; P3 requires broader conversion.
- Keep `Ctrl+D` (existing generic duplicate) active. `Shift+D` is an additional panel-oriented candidate.

## B1 implementation note (pending verification)

- Branch: `team-a/panel-grab-b1`
- Initial scope: selected **whole panels**, `G` move, free/X/Y constrained mouse movement, signed numeric offsets (`G 25` = +25,+25), `-` before or after digits, Enter/left-click commit, Esc/right-click cancel.
- `X` when idle deletes **only selected panels**. While G is active it is exclusively axis selection and cannot delete.
- Preview updates item positions without history. One history entry is recorded on non-zero commit; cancellation restores original positions.
- Modal previews are cancelled when scene/page changes, focus leaves canvas, or editor mode changes.
- `G` on enabled corner-edit handles deliberately displays a not-yet-supported notice; vertex/edge movement is B2, and **is not claimed in B1**.
- Synthetic offscreen regressions are in `tests/test_panel_grab.py`. Windows interactive GUI validation remains required.

## B2 selected corner/edge G (verification pending)

- Extends the B1 modal lifecycle to selected corner handles (`Tab` ON); Shift+edge selection is the existing two-handle representation of a selected edge.
- Multiple selected handles, including across selected panels, move using one scene delta. Constrained and numeric moves are in scene/page axes, then converted to each panel's local axes.
- The current quadrilateral geometry is validated during preview; excessive movement is limited to preserve minimum edge length, non-crossing and convexity. All selected handles in one panel use the same scale factor to avoid shearing.
- Esc/right click restores original vertex positions without history; Enter/left click commits exactly one snapshot and recalculates transform origin.
- Note: this B2 patch uses **validity limiting but not snap-to-other-panel** during G. Existing pointer-drag snap behavior is unchanged. Dedicated G snapping parity remains a follow-up acceptance condition before P1 is fully complete.
- Windows GUI validation remains outstanding; do not mark B2 fully accepted based on code alone.

## P2 interactive Ctrl+R quad split — implementation record

- Source: `CanvasView` enters a non-mutating `_panel_split_preview` state after `Ctrl+R` for exactly one selected valid four-corner panel.
- Mouse horizontal movement selects `vertical` (left/right pieces); vertical movement selects `horizontal` (upper/lower pieces), in panel-local coordinates (rotated panels behave consistently). Current N-split UI orientation is the initial default.
- View foreground draws dashed orange centerlines in scene coordinates; red marks a split that is too narrow/invalid.
- Mouse wheel changes count in range **2–12**. The preview never adds or removes scene items, and adds no Undo entry.
- Left click commits via existing `MangaEditor.split_selected_panel_n()`, preserving established gap setting, image-inheritance mode, rotation/scale path and single Undo transaction. Invalid split stays in preview. Esc/right-click cancels with no scene/history changes.
- Existing N-split and advanced split buttons are preserved.
- For diagonally selected vertex pairs, `Ctrl+R` deliberately declines automatic splitting until P4 two-vertex chord mode exists. N-gons similarly wait until P3/P4.
- New synthetic offscreen tests: `tests/test_panel_split_preview.py` covering orientation, wheel limits, rect gutter, freeform quad splitting, invalid geometry, cancel, text-focus precedence, modal G/X suppression, and scene changes.

### P2 manual Windows acceptance checklist (still pending)

1. Focus canvas; select one ordinary rectangular panel and press Ctrl+R; confirm dashed line appears, no scene change.
2. Move mouse horizontally/vertically; verify orientation switches; use wheel to adjust 2..12.
3. Left-click; verify count and gap, then Ctrl+Z and Ctrl+Y restore the split as one action.
4. Repeat on a skewed/freeform quad containing an image and effects; verify clipping/content/transform and chosen image inheritance setting.
5. Attempt an impossible split (too many panels with large gutter); verify red preview and no deletion.
6. Press Esc and right-click on separate attempts; verify exact restoration, no history entry; test Ctrl+R during text edit.
7. Switch page/mode or remove selected panel during preview; ensure preview safely disappears.

## P3 — N-gon panel data + selected-edge E midpoint (CI / Windows QA pending)

- New standalone `app.py` geometry helpers (no extra module needed by drag/drop updater): validate simple polygons with >=3 vertices, non-zero area, minimum edge lengths, and no edge crossings/touches/backtracking. Concave shapes and collinear forward points from midpoint insertion are valid.
- `PanelItem.custom_points` now accepts serialized polygons with 3+ finite vertices, while old four-corner projects continue to load unchanged. Existing `panel_path()` already uses `QPolygonF` for image/effect clipping and supports concavity.
- Paint/foreground selected edges, edge hit tests, edge selection, snapping to other panels' edges, selected-handle movement and save/deserialize handle arbitrary polygon lengths.
- In `Tab` corner mode: `Shift+edge click` selects adjacent endpoints; `E` inserts a vertex at their midpoint without moving the outline, selects the new vertex, and records one Undo entry. Repeating produces 5, 6, 7... vertices. `G` edits the new vertex, using full-polygon validity limiting to reject self-crossings.
- Quad-specific parallel snap and old quad N-split/grid/advanced split remain unchanged and are **guarded for N-gons**, not silently extrapolated; polygon chord splitting is P4.
- Synthetic regression: `tests/test_panel_polygon.py`, including concave clipping, save/load, copy independence, invalid geometry, E insertion and G editing.
- Manual Windows acceptance still needed: add 5/6+ vertices to an image panel; move into a concave polygon; save/reopen and Undo/Redo; verify effect overlays and PNG/ZIP clipping; ensure old quad N-split works. Do not mark P3 fully accepted without evidence.
