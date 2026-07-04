# GIANTS i3d Importer for Blender

Blender 4.5+ addon that imports GIANTS Engine `.i3d` scenes (Farming Simulator 25) — meshes, armatures, vertex skinning, materials, and a round-trip helper that fixes exporter-assigned node IDs so original animations keep working.

> **No public Blender importer for FS25 i3d existed before this.** Other tools are export-only.

> 📖 **New here or juggling different animals/file types? See [HOW-TO.md](HOW-TO.md)** — step-by-step workflows, an options reference, the animal→animation table, and troubleshooting.

## ⚠️ Disclaimer

This addon and the `fix-i3d.bat` / `graft-i3d.bat` round-trip helpers have **only been tested on FS25 animal `.i3d` files** (cattle, etc.). They have **not** been tested on vehicles, buildings, props, maps, or any other type of i3d file. They may or may not work for those — the file format is similar but specifics (mesh layouts, material setups, attachments, lights, joints) vary, and some of those paths in the importer are stubbed or untested.

If you hit a problem on a different kind of i3d, **please open a GitHub issue** with:

- The Blender version you're using
- The full error message / Python traceback (turn on the System Console first: **Window → Toggle System Console**)
- A short description of the i3d (vehicle / building / prop / map / etc.)
- If at all possible, the offending `.i3d` file (and `.i3d.shapes` if applicable) attached or linked

Without those details I can't reproduce the bug, and the issue will likely just sit.

## Features

- **Scene import** — full hierarchy from the `.i3d` XML (TransformGroups become Empties, with optional axis conversion to Blender's Z-up).
- **Meshes** — decrypts `.i3d.shapes` (FS25 v9 and v10 ciphers) and builds Blender meshes with positions, triangles, normals, UVs, and per-shape materials.
- **Armature & skinning** — detects the bone set via `skinBindNodeIds`, builds an Armature with derived tail/roll, binds skin weights via vertex groups + Armature modifier without breaking the scene hierarchy. Each bone carries its original i3d translation/rotation/scale as custom properties so an exporter can round-trip the joint matrices exactly.
- **Materials** — Principled BSDF with diffuse/normal/gloss textures resolved through `$data` / `$dataS` paths from the addon preferences.
- **Round-trip for animated models (`graft-i3d.bat`)** — for mesh-only edits, grafts the pristine skeleton from the game's animation `.i3d` onto your re-exported mesh so the original `.i3d.anim` plays correctly in GIANTS Editor. This is the recommended path for anything that has to keep its stock animation.
- **Node-ID remap (`fix-i3d.bat`)** — simpler helper that only rewrites exporter-assigned node IDs to match an original `.i3d`. Fine for non-animated round-trips; for animated models use `graft-i3d.bat` instead (see below for why).
- **Animation import into Blender (experimental)** — opt-in. The track structure is decoded but the per-keyframe rotation values aren't fully reverse-engineered yet, so motion played *inside Blender* may look wrong. (Getting a re-exported model to play its stock anim in GIANTS Editor is a separate, solved workflow — see `graft-i3d.bat`.)

See [updates.txt](updates.txt) for the recent fix log.

## Install

1. Download the `io_import_i3d.zip` from the latest release (or zip the `io_import_i3d/` folder yourself).
2. In Blender: **Edit → Preferences → Add-ons → Install...** and select the zip.
3. Enable **Import-Export: GIANTS i3d Importer**.
4. (Optional) In the addon preferences set the `$data` and `$dataS` paths to your FS25 install root and the `dataS` folder so textures resolve.

## Use

### Import a scene

**File → Import → GIANTS i3d (.i3d)**, pick the `.i3d` file. Sibling `.i3d.shapes` and (optionally) `.i3d.anim` are picked up automatically.

Import options:

| Option | Default | Notes |
|---|---|---|
| Import animations (experimental) | off | See *Known limitations* below |
| Import materials | on | Set `$data`/`$dataS` in addon prefs to resolve textures |
| Bone display size (m) | 0.05 | Length used for leaf bones with no children |
| Axis convention | Auto (Y-up → Z-up) | Use **None** to keep raw GIANTS coordinates (debug only) |
| Forward axis | -Y | Direction the model faces in Blender after import |
| Wrap in container empty | off | Adds an extra root Empty named after the file. **Disable for round-trip with original animations** — adding a wrapper shifts every node ID. |

### Keep the stock animation on a re-exported model — `graft-i3d.bat` (recommended)

**What it's for:** mesh-only edits (re-skin / re-texture / reshape) where you want the animal's **stock animation** to keep playing.

**Why a plain node-ID remap isn't enough.** FS keeps each animal's animation in a *separate, skeleton-only* file (e.g. `cattleCalfAnimations.i3d`, which ends in `<Animation externalAnimFile=...>`); `animals.xml` marries it to a model via `animation="..."` + `skeletonIndex`. Two independent things have to line up:

1. **Node IDs** — the `.i3d.anim` addresses bones by `nodeId` **in the animation file's numbering** (e.g. calf `calf_root=4`, `spine=5`), *not* the model's (`cattleCalfHolstein` has `calf_root=20`). Point it at the wrong ids and the anim drives the wrong bones — the mesh flails.
2. **Rest pose** — the keyframes are absolute local transforms, and GIANTS builds the skin bind from the model's rest pose. A Blender round-trip mangles bone rest orientations, so the bind disagrees with the anim and the mesh distorts. (Blender can't hold an arbitrary joint frame *and* draw bones down the chain, so this can't be fixed by editing the exported skeleton — see [updates.txt](updates.txt).)

`graft-i3d.bat` solves both by grafting a clean skeleton whose **rest pose comes from the stock model** (the pose your mesh was skinned to) and whose **node IDs come from the animation `.i3d`** (the ids the `.anim` references), then re-points your mesh's skin to it by bone name and adds the `<Animation>` reference so GIANTS Editor loads the clip. The Blender-mangled skeleton just never ships.

> **Why two files (model + animation):** some animals (e.g. Highland) ship an animation i3d whose skeleton is in a *different rest pose* than the model. Grafting the anim skeleton would deform the mesh; taking **rest from the model** and **ids from the animation** is correct for every animal — it's exactly what the game does (loads the model's skeleton and applies the anim to it).

**Steps:**

1. Export your edited model from Blender — make sure **all** mesh parts export (body, eyes, teeth, ears, hair, …), not just the body.
2. **Drag the exported `.i3d` onto `graft-i3d.bat`.**
3. Picker 1: choose the stock **model** `.i3d` (the one you imported from — for the correct rest pose).
4. Picker 2: choose the **animation** `.i3d` (paired with the `.i3d.anim` — for the node ids). The `.anim` must sit next to it.
5. Open the `<exported>_GE.i3d` it writes in GIANTS Editor and play the animation.

CLI alternative:

```
python io_import_i3d/tools/graft_skeleton.py <model.i3d> <exported.i3d> <output.i3d> --id-source <animation.i3d> --anim-ref <animation>.i3d.anim
```

> **Two gotchas:** the graft only keeps what's in the export, so verify every mesh part is present first (the calf has 16 skinned shapes). And the collision proxy comes out of Blender without its collision flags (`kinematic`, `collisionFilterGroup`, `nonRenderable`) — restore those from the stock proxy before shipping.

### Node-ID remap only — `fix-i3d.bat`

**What it's for:** re-syncing skin nodes so the original `.i3d.anim` keeps working on a re-exported model. **For animated models prefer `graft-i3d.bat` above** — a node-ID remap alone leaves the mangled rest pose and, for animals, targets the model's ids rather than the animation file's, so the anim still won't play right.

GIANTS animations reference bones by their numeric `nodeId` from the original i3d. When you re-export from Blender, the exporter (StjerneIdioten's, etc.) assigns its own node IDs, which **breaks animation playback** in GIANTS — you'll get errors like `Animation set ... skipped. Transform group id N not found.` `fix-i3d.bat` rewrites the exporter's node IDs to match the originals so the existing `.i3d.anim` lines up again.

**You need both files:**
- The **original** `.i3d` (the one you imported from — provides the canonical node IDs).
- The **exported** `.i3d` (the one Blender just wrote out — has fresh, mismatched IDs).

**Steps:**

1. Export your edited model from Blender (e.g. with StjerneIdioten's i3d exporter).
2. **Drag the exported `.i3d` onto `fix-i3d.bat`** in this repo.
3. A file picker opens — select the **original** `.i3d`.
4. The script writes `<exported>_fixed.i3d` next to your export.
5. Open the `_fixed.i3d` in GIANTS Editor — the original `.i3d.anim` will play correctly.

CLI alternative:

```
python io_import_i3d/tools/remap_node_ids.py <original.i3d> <exported.i3d> <output.i3d>
```

> **Note:** matching is by bone/node `name`. If you renamed a bone in Blender, edit the export's name back to match the original before running the fix.

## Known limitations

- **Importing animation *into Blender* is experimental.** The track header and bone-mapping are correctly decoded, but the per-keyframe rotation encoding (last 3 of 6 floats per KF) doesn't match any standard rotation parameterisation we tested, so the rig flails when you play it *in Blender*. Off by default. (This is unrelated to `graft-i3d.bat`, which makes a re-exported model play its stock anim correctly *in GIANTS Editor* — that works.)
- **Only the first clip parses.** Multi-clip animation files (e.g. `cattleCalfAnimations` has 41 clips) currently only read clip 0 cleanly. Note: the `.i3d.anim` header has a 4-byte alignment pad after the character-name string before `clip_count` that `anim_reader.parse_anim` doesn't yet account for — fix this when resuming the in-Blender animation work.
- **Round-trip caveats.** For animated models, use `graft-i3d.bat` (grafts the anim file's skeleton) — the plain `fix-i3d.bat` remap leaves the mangled rest pose and targets the wrong id space. Both tools match bones **by name**, so if you renamed a bone in Blender they can't match it — keep names identical to the original.
- **Collision proxy loses its flags on export.** i3dio writes the proxy without `kinematic` / `density` / `collisionFilterGroup` / `nonRenderable`; restore them from the stock proxy before shipping a mod.
- **Lights, cameras, particles, joints** — out of scope. Imported as plain Empties at correct transforms but not converted to Blender equivalents.

## License

GPL-2-or-later. See [LICENSE](LICENSE).

## Credits

- [Donkie/I3DShapesTool](https://github.com/Donkie/I3DShapesTool) — C# (MIT) library; we ported the cipher and shape-entity parsing to Python.
- [StjerneIdioten/I3D-Blender-Addon](https://github.com/StjerneIdioten/I3D-Blender-Addon) — Python (GPL-2) export-only addon; structural inspiration for the addon layout.
