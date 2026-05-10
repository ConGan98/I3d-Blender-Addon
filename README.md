# GIANTS i3d Importer for Blender

Blender 4.5+ addon that imports GIANTS Engine `.i3d` scenes (Farming Simulator 25) — meshes, armatures, vertex skinning, materials, and a round-trip helper that fixes exporter-assigned node IDs so original animations keep working.

> **No public Blender importer for FS25 i3d existed before this.** Other tools are export-only.

## ⚠️ Disclaimer

This addon and the `fix-i3d.bat` round-trip helper have **only been tested on FS25 animal `.i3d` files** (cattle, etc.). They have **not** been tested on vehicles, buildings, props, maps, or any other type of i3d file. They may or may not work for those — the file format is similar but specifics (mesh layouts, material setups, attachments, lights, joints) vary, and some of those paths in the importer are stubbed or untested.

If you hit a problem on a different kind of i3d, **please open a GitHub issue** with:

- The Blender version you're using
- The full error message / Python traceback (turn on the System Console first: **Window → Toggle System Console**)
- A short description of the i3d (vehicle / building / prop / map / etc.)
- If at all possible, the offending `.i3d` file (and `.i3d.shapes` if applicable) attached or linked

Without those details I can't reproduce the bug, and the issue will likely just sit.

## Features

- **Scene import** — full hierarchy from the `.i3d` XML (TransformGroups become Empties, with optional axis conversion to Blender's Z-up).
- **Meshes** — decrypts `.i3d.shapes` (FS25 v9 cipher) and builds Blender meshes with positions, triangles, normals, UVs, and per-shape materials.
- **Armature & skinning** — detects the bone set via `skinBindNodeIds`, builds an Armature with derived tail/roll, binds skin weights via vertex groups + Armature modifier without breaking the scene hierarchy.
- **Materials** — Principled BSDF with diffuse/normal/gloss textures resolved through `$data` / `$dataS` paths from the addon preferences.
- **Round-trip** — drag-and-drop helper to remap node IDs in an exported `.i3d` so it lines up with the original `.i3d.anim`.
- **Animations (experimental)** — opt-in. The track structure is decoded but the per-keyframe rotation values aren't fully reverse-engineered yet, so motion may look wrong.

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

### Round-trip back to GIANTS Editor — `fix-i3d.bat`

**What it's for:** re-syncing skin nodes so the original `.i3d.anim` keeps working on a re-exported model.

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

- **Animations are experimental.** The track header and bone-mapping are correctly decoded, but the per-keyframe rotation encoding (last 3 of 6 floats per KF) doesn't match any standard rotation parameterisation we tested. The rig flails when animation is on. Off by default; toggle on at your own risk.
- **Only the first clip parses.** Multi-clip animation files (e.g. cattleAdultAnimations has 41 clips) currently only read clip 0 cleanly; subsequent clips fail mid-parse and are silently skipped.
- **Round-trip caveats.** The remap script handles node IDs and (X-axis-180) vertex rotation. If you renamed a bone in Blender, the script can't match it — keep names identical to the original.
- **Lights, cameras, particles, joints** — out of scope. Imported as plain Empties at correct transforms but not converted to Blender equivalents.

## License

GPL-2-or-later. See [LICENSE](LICENSE).

## Credits

- [Donkie/I3DShapesTool](https://github.com/Donkie/I3DShapesTool) — C# (MIT) library; we ported the cipher and shape-entity parsing to Python.
- [StjerneIdioten/I3D-Blender-Addon](https://github.com/StjerneIdioten/I3D-Blender-Addon) — Python (GPL-2) export-only addon; structural inspiration for the addon layout.
