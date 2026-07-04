# How to use — GIANTS i3d importer + round-trip tools

A practical, scenario-driven guide. For the feature list and install, see the
[README](README.md). For the fix log, see [updates.txt](updates.txt).

## Contents

- [The pieces](#the-pieces)
- [One-time setup](#one-time-setup)
- [Scenario 1 — Edit a mesh and keep the stock animation](#scenario-1--edit-a-mesh-and-keep-the-stock-animation) ← the main workflow
- [Scenario 1b — Resize the animal (bigger/smaller variant)](#scenario-1b--resize-the-animal-biggersmaller-variant)
- [Scenario 2 — Just look at / understand a model](#scenario-2--just-look-at--understand-a-model)
- [Scenario 3 — Preview the animation inside Blender](#scenario-3--preview-the-animation-inside-blender)
- [Scenario 4 — Growth / variant meshes on one skeleton](#scenario-4--growth--variant-meshes-on-one-skeleton)
- [Import options reference](#import-options-reference)
- [The tools reference](#the-tools-reference)
- [Which animation file goes with which animal](#which-animation-file-goes-with-which-animal)
- [Troubleshooting](#troubleshooting)

---

## The pieces

| Piece | What it's for |
|---|---|
| **Blender addon** (`io_import_i3d`) | Import a `.i3d` into Blender — mesh, skeleton, skin, materials, attributes, (optionally) animation |
| **i3dio** (StjerneIdioten, separate addon) | **Export** back to `.i3d`. You need this installed. |
| **`graft-i3d.bat`** | After a mesh edit, make the re-exported model play its **stock animation** in GIANTS Editor. **This is the one you'll use most.** |
| **`fix-i3d.bat`** | Older node-ID remap only. Rarely needed now — prefer `graft-i3d.bat` for anything animated. |

---

## One-time setup

1. Install this addon (README → Install) and enable it.
2. Install **StjerneIdioten's i3dio** exporter and, in **its** preferences, point it at your FS install so it can find game shaders/paths.
3. In **this** addon's preferences, set the `$data` / `$dataS` paths so textures resolve.

---

## Scenario 1 — Edit a mesh and keep the stock animation

The bread-and-butter workflow: take a stock animal, change the mesh/textures
(make a variant), and have it still animate in-game.

### Step 1 — Import the stock model

**File → Import → GIANTS i3d**, pick the model (e.g. `cattleHighland.i3d`).
Recommended options for this workflow:

- ✅ Import materials, ✅ Import i3d attributes, ✅ Preserve order (01_ prefix)
- ⬜ Import animations (not needed just to edit the mesh)
- ⬜ Wrap in container empty

Keep the sibling `.i3d.shapes` next to the model (that's the geometry).

### Step 2 — Edit the mesh in Blender

Change what you need (reshape, re-skin, re-texture, remove parts, …). **Don't
rename bones** — skinning and the animation match them by name.

### Step 3 — Export with i3dio

Export the whole model. **Make sure every mesh part exports** (body, eyes,
teeth, ears, hair, …) — a body-only export is the classic "half the model is
missing" mistake.

### Step 4 — Graft the skeleton back (`graft-i3d.bat`)

Blender/i3dio mangle the skeleton's rest pose and renumber nodes, which breaks
the stock animation. `graft-i3d.bat` fixes it:

1. **Drag your exported `.i3d` onto `graft-i3d.bat`.**
2. **Picker 1** → the stock **model** `.i3d` you imported from (for the correct rest pose).
3. **Picker 2** → the **animation** `.i3d` for this animal (for the node ids). Its `.i3d.anim` must sit next to it.
4. It writes `<exported>_GE.i3d` and copies the `.anim` beside it.

Open **`<exported>_GE.i3d`** in GIANTS Editor and play — it should animate.

> ⛔ **Do NOT edit or re-save the `_GE.i3d` (or a `_fixed.i3d`) afterwards.**
> Saving the file in GIANTS Editor — or editing it any other way — **renumbers
> the nodes**, which destroys the exact node ids the `.i3d.anim` depends on, and
> the animation stops working. The graft/fix output is the **final** step. If you
> need to change anything, go **back to Blender**, edit, re-export, and run
> `graft-i3d.bat` again — don't patch the grafted file in place. (Opening it in
> GIANTS Editor just to *look* is fine; it's **saving** that breaks it.)

> **Why two files?** The graft takes the skeleton **rest pose from the model**
> (the pose your mesh is skinned to) and the **node ids from the animation i3d**
> (the ids the `.anim` references). Some animals (e.g. Highland) ship an
> animation skeleton in a *different* rest pose than the model, so you must take
> rest from the model — exactly what the game does.

### Step 5 — Ship it

Put the `_GE.i3d` (renamed as you like) in your mod with its textures, and wire
it up in `animals.xml` with the right `skeletonIndex` / `meshIndex` /
`proxyIndex` for its top-level node order, and `animation="…"` pointing at the
animation i3d. **Restore the collision proxy's flags** if needed (see
Troubleshooting).

> **Don't run `fix-i3d.bat` for this.** The graft is the complete fix. Graft the
> **raw** export, not a `_fixed` file.

---

## Scenario 1b — Resize the animal (bigger/smaller variant)

Making a **bigger bull** (or a smaller/dwarf variant) is Scenario 1 with one
extra concern: the `.i3d.anim` stores **stock-sized** bone positions, so if the
skeleton is a different size the animation fights it.

### In Blender

1. Import the stock model (Scenario 1, Step 1).
2. **Scale the whole rig AND mesh together, uniformly.** Select the armature and
   all mesh parts and scale them by the same factor (e.g. `S` on all axes).
   Keep it **uniform** — scaling more on one axis than another can't be
   reconciled with a stock animation and will distort on play.
3. **Apply the scale before exporting.** In Object Mode select everything →
   **Object → Apply → Scale** (`Ctrl+A → Scale`). This bakes the new size into
   the geometry and bone positions and resets each object's scale back to
   **1.000**. Do this so the export carries the real size (not a pending object
   scale the exporter might handle inconsistently). Confirm the **N-panel →
   Item → Scale reads `1.000`** on the armature and meshes before you export.
4. Export with i3dio as normal.

### Graft it (handles the resize automatically)

Run **`graft-i3d.bat`** exactly as in Scenario 1. It **auto-detects** the
uniform scale (from the bone spacing vs the stock model) and puts that scale on
the **skeleton root**, then grafts the clean stock skeleton underneath. Because
the scale sits *above* the animated bones, the stock animation plays **at your
model's size** instead of snapping back to stock. The batch file has this on by
default (`SCALE_FROM_EXPORT=--scale-from-export` near the top).

> **Why not just move/scale the bones and leave it?** The animation overwrites
> each bone's position every frame with the stock value, so a resized skeleton
> gets "downsized" back to stock the moment you press play. The scale has to be
> on the **root** (above the animation), which is what the graft does.

**Check at rest first:** open the `_GE.i3d` in GIANTS Editor and look at the
model *before* playing — mesh should sit correctly on the skeleton. If it does,
playback holds that size. If it looks off at rest, your mesh and rig were scaled
by *different* amounts — re-scale them together in Blender and re-export.

---

## Scenario 2 — Just look at / understand a model

Import with defaults. Turn on **Import i3d attributes** to see collision /
physics / shader settings in the Object, Data, and Material panels (needs
i3dio installed). Nothing to export.

---

## Scenario 3 — Preview the animation inside Blender

> ⚠️ **Experimental / partially working.** The `.i3d.anim` format is decoded and
> most clips play, but some clips still come out wrong. Use for a rough preview,
> not as ground truth.

1. **File → Import → GIANTS i3d**, pick the **model** (mesh + skeleton).
2. Options:
   - ✅ Import animations
   - **Animation i3d** → leave blank to auto-detect a sibling animation i3d, or point it at the animation `.i3d` for this animal (its `.i3d.anim` must be beside it)
   - ⬜ **Exact bone orientation** → leave OFF (normal bones animate correctly now)
3. Press **Spacebar** to play. Switch clips in **Dope Sheet → Action Editor**
   (actions are named `i3dAnim_<clip>`); each loops automatically.

Tip: if the animation i3d isn't a sibling, copy it (and its `.i3d.anim`) into the
model's folder so auto-detect finds it.

---

## Scenario 4 — Growth / variant meshes on one skeleton

Put several meshes (e.g. `body` and a bulkier `body_pedigree` bull frame) on the
**same skeleton and animation**, and let your game-side code toggle which shows.

**The one rule:** a shared skeleton means shared **joint positions**. Bulk is
free (deeper chest, thicker neck, more muscle — those ride the bones), but keep
**limb proportions the same** (knees/hocks must bend where the stock cow does).
Want the pedigree individual overall *bigger*? Use the game's per-animal uniform
scale — don't bake a bigger skeleton (it would break the other mesh).

**Steps:**

1. **Import** the stock model (`body` + skeleton).
2. **Make the variant** — duplicate `body` and sculpt it bulkier, or bring in a
   whole new mesh. Keep the joints lined up with the bones.
3. **Skin it** — in the **N sidebar → i3d tab → i3d Tools → Skinning**: select
   your new mesh(es), then **shift-select the already-skinned `body` LAST** (so
   it's active), and click **Skin to i3d Armature**. This copies `body`'s weights
   across by nearest surface, adds the vertex groups + Armature modifier, and
   binds it to the same skeleton. Clean up any seams by hand.
4. **Set default visibility** — select a mesh and use **i3d Tools → i3d
   Visibility → Visible / Hidden** to set which variant ships visible (your game
   code flips it at runtime, like the horns trick). This sets `hide_render`,
   which is what i3dio exports as `visibility`.
5. **Export** both meshes with i3dio, then **graft** as usual (the graft
   re-points *every* skinned mesh, so both come through).

Both meshes deform from the one skeleton, so they share the stock animation for
free. Big proportion changes (a calf vs an adult) still need their **own** rig —
that's why the game ships separate calf/adult skeletons.

> **Watch the small pinned meshes (eyes, tongue, teeth).** These ride tiny fixed
> bones (`cow_eyeball_*`, `cow_tongue_*`) in the head/mouth. If you enlarge the
> **head**, those bones don't move, so the eyes/tongue no longer line up (and
> the eye pivots around the stock spot when animated). Fix: **keep the head/face
> region at stock size** and bulk the *body* only — a pedigree bull reads as
> bigger through the chest/shoulders/neck, not a bigger face. (If the head must
> grow, the eyes/tongue effectively need their own rig — the "different frame"
> case.)

## Import options reference

| Option | Default | Notes |
|---|---|---|
| Import materials | on | Principled BSDF + textures. Needs `$data`/`$dataS` in prefs. |
| Import i3d attributes | on | Copies GIANTS attrs (collision, density, shadows, rigid body, shader) into i3dio's Object/Data/Material panels so they round-trip. No-op without i3dio. |
| Import animations | off | See Scenario 3. |
| Animation i3d | blank | The animation `.i3d` (auto-detected if blank). Accepts the `.i3d.anim` too. |
| Exact bone orientation | off | Leave off — bones point down the chain and still animate correctly. On = raw joint frames (bones point sideways). |
| Bone display size | 0.05 | Visual only. |
| Axis convention / Forward axis | Auto / -Y | Leave as-is unless debugging orientation. |
| Preserve order (01_ prefix) | on | Renames objects `01_`, `02_`, … per parent so the outliner keeps i3d order. Bones aren't renamed; round-trip tools strip the prefix. |
| Wrap in container empty | off | Adds a root empty; shifts node ids. Leave off for round-trip. |

---

## The tools reference

### `graft-i3d.bat` — keep the stock animation (recommended)

Rest from the model, ids from the animation i3d. See Scenario 1.

CLI:
```
python io_import_i3d/tools/graft_skeleton.py <model.i3d> <exported.i3d> <output.i3d> --id-source <animation.i3d> --anim-ref <animation>.i3d.anim
```

Add **`--scale-from-export`** if you resized the animal (Scenario 1b) — it scales
the grafted skeleton root to match your model so the stock animation plays at the
new size. `graft-i3d.bat` passes it by default; harmless (no-op) on unscaled
models.

### `fix-i3d.bat` — node-ID remap only (legacy)

Rewrites exporter-assigned node ids to match an original i3d. Fine for
non-animated round-trips; for anything animated use `graft-i3d.bat` instead
(a remap alone leaves the mangled rest pose and the wrong id space).

CLI:
```
python io_import_i3d/tools/remap_node_ids.py <original.i3d> <exported.i3d> <output.i3d>
```

---

## Which animation file goes with which animal

The animation lives in a **separate** skeleton-only i3d (`…Animations.i3d`) plus
its `.i3d.anim`. The pairing is set in the game's `animals.xml` (`animation="…"`).
Rule of thumb — **match the animal, and match the bone-name prefix**:

| Animal | Animation i3d | Bone prefix |
|---|---|---|
| Calf / kid (holstein calf, …) | `cattleCalfAnimations.i3d` | `calf_*` |
| Baby calf | `cattleCalfBabyAnimations.i3d` | `calf_*` |
| Adult cow (holstein, angus, limousin, swiss brown) | `cattleAdultAnimations.i3d` | `cow_*` |
| **Highland** (adult) | **`cattleHighlandAnimations.i3d`** (its own — different rest pose) | `cow_*` |
| Water buffalo | `waterBuffalo…Animations.i3d` | (buffalo joints) |

If you graft with the **wrong** animation file, the bone names won't match and
the animation binds to nothing (0 tracks matched). If in doubt, open the
animation i3d and check the bone-name prefix matches your model's bones.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Animation worked, then broke after I edited/saved the `_GE.i3d` | Saving the grafted file (e.g. in GIANTS Editor) **renumbers the nodes** and breaks the `.anim` id binding. Never edit/save the graft output — go back to Blender, re-export, and re-graft. |
| Animation "runs but is all over the place" | Wrong animation file (bone names/ids don't match), or you grafted the anim skeleton when the model rest differs — use `graft-i3d.bat` (model rest + anim ids). |
| Mesh explodes/distorts on play | Skeleton rest doesn't match the mesh bind — graft **rest from the model**, not the animation i3d. |
| Resized model **shrinks to stock size when the animation plays** | The `.anim` carries stock-sized bone positions. Graft with **`--scale-from-export`** (default in `graft-i3d.bat`) so the scale goes on the skeleton root, above the animation. Also make sure you **applied scale** (`Ctrl+A → Scale`) in Blender and scaled the mesh + rig **uniformly** (Scenario 1b). |
| Resized model's eyes/small parts swing too far | Same cause — regraft with `--scale-from-export` so bones and mesh share one scale (a stock-size skeleton under a bigger mesh gives the eyes a lever-arm). |
| Half the model missing after graft | Not all mesh parts exported from Blender. Re-export the full model; the graft only keeps what's in the export. |
| Materials/shader panel empty in Blender | i3dio can't find the shader — set i3dio's FS/shader path in its preferences, then re-import. Console says `shader X not found by i3dio`. |
| Collision proxy has no rigid-body / filter flags after export | i3dio drops proxy collision flags on export. Restore `kinematic` / `collisionFilterGroup` / `nonRenderable` from the stock proxy (importing with **Import i3d attributes** shows what they should be). |
| Outliner order scrambled | Turn on **Preserve order (01_ prefix)** at import. |
| Changed the addon code and it didn't take effect | Blender caches modules — **fully quit and reopen Blender**; a re-run in the same session uses the old code. |
| GIANTS Editor: "Transform group id N not found" | The model's skeleton doesn't carry the id the `.anim` needs — regraft with the correct **animation i3d** as the id source. |
