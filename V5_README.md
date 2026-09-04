# Pokémon Black 2 Runtime — Map v5 overlay

Base audited repository commit: `410a75d09d123ea07420377de8636c77910ff62f`.

This package is a **code overlay**, not a ROM and not a copy of your 39 evidence dumps. It is designed to be extracted beside an existing `ashanzzz/pokemon-black2-runtime` checkout and installed with `tools/install_v5.py`.

## Why v5 exists

v4 mixed three different questions:

1. What does the original ROM define?
2. Which resources are currently loaded?
3. What is the player/NPC/door state right now?

v5 separates them. This is both more correct and much cheaper at runtime.

| Data | Source | Refresh policy |
|---|---|---|
| ZoneHeader / AreaHeader | ROM | once / lazy cache |
| Matrix + full Chunk ID table | ROM | once / lazy cache |
| Terrain BMD0 / permission raw planes | ROM | once / asset cache |
| Building placement + Building NSBMD + BTX0 | ROM | once / asset cache |
| Static NPC / Warp / Trigger / Furniture definitions | ROM | once / lazy cache |
| Player position / facing / movement | RAM FieldActor | realtime |
| PlayerState.ZoneID | RAM | realtime / RuntimeHub cadence |
| Current mapper / player chunk / loaded chunk lifecycle | RAM FieldG3DMapper | realtime or on change |
| Runtime NPC positions / spawn state | RAM FieldActorSystem | realtime when needed |
| Runtime Prop / Door instances | RAM FieldPropSystem | on zone/chunk change; not every frame |
| Door→Warp destination semantics | ROM + cross-frame RAM transition | learn/verify on transition |

**Rule:** ROM describes the world. RAM proves which part of that world is current and what is changing now.

## Confirmed v4 format errors fixed by v5

Gen-5 ZoneData is 0x30 bytes. The important fields are:

- `+0x02 = areaID`
- `+0x04 = matrixID`
- `+0x16 = entitiesID`

The old code used `+0x02` as matrix and `+0x10` as event identity. v5 uses the correct fields.

A Gen-5 Matrix is:

```text
u32 hasZones
u16 width
u16 height
u32 chunkIds[width*height]
if hasZones:
    u32 zoneIds[width*height]
```

The old "definition table" name is therefore not authoritative. v5 treats it as the zone table only when `hasZones == 1`.

A normal map chunk is a Game Freak container. `file0` is the terrain NSBMD and the final file is decoded as `ChunkBuildings` when structurally valid. Building placements are not procedurally invented.

## Install

From the extracted v5 package:

```bash
python tools/install_v5.py --repo D:\path\to\pokemon-black2-runtime
```

The installer:

- copies the new v5 modules and UI;
- patches `app.py` to add the v5 router;
- changes API version to 5.0.0;
- fixes the known silent `candidates[0]` BTX0 ambiguity in `native_map.py`;
- fixes the legacy `rom_maps.py` ZoneHeader matrix offset;
- fixes legacy `map_knowledge.py` ZoneHeader area/matrix/entities fields;
- patches future Universal Evidence captures to emit `runtime_field_v2.json` + `map_truth_v3.json` from the already captured Main RAM (zero extra RAM reads);
- writes `.v4bak` backups before modifying existing files.

Then set your own legal ROM path:

```bat
set BLACK2_ROM_PATH=D:\roms\pokemon_black2.nds
```

or PowerShell:

```powershell
$env:BLACK2_ROM_PATH = 'D:\roms\pokemon_black2.nds'
```

Do **not** commit the ROM.

## Test

```bash
python -m unittest tests.test_gen5_rom_map_v5 -v
```

or, if pytest is installed:

```bash
pytest -q tests/test_gen5_rom_map_v5.py
```

## Build all static original-map metadata once

This does not connect to BizHawk and does not read RAM:

```bash
python tools/export_original_world_v5.py --rom D:\roms\pokemon_black2.nds
```

It writes one JSON descriptor per Zone under `runtime/original_world_v5/`. Terrain/building GLB conversion remains lazy/on-demand to avoid converting thousands of assets that may never be viewed.

## Re-index the 39 existing RAM dumps

No recapture is needed:

```bash
python tools/reindex_dumps_v5.py --rom D:\roms\pokemon_black2.nds
```

Derived output is written outside the evidence folders:

```text
reverse_engineering/derived/v5/
  snapshots/<snapshot_id>.json
  transition_evidence_v1.json
```

Original `main_ram.bin`, `integrity.json`, screenshots and ZIPs are not modified.

Future dumps created after installing v5 additionally include:

```text
runtime_field_v2.json   # same physical Main RAM frame
map_truth_v3.json       # ROM join if BLACK2_ROM_PATH is available
```

These are derived from the RAM bytes already written to disk; they do not issue another emulator-memory read.

## Endpoints

After restarting the backend:

- `/original-map` — v5 browser page
- `/api/v1/map/v5/status`
- `/api/v1/map/v5/catalog`
- `/api/v1/map/v5/zone/{zone_id}` — static original ROM world
- `/api/v1/map/v5/current` — RAM current identity joined to cached ROM world
- `/api/v1/map/v5/terrain/{zone}/{x}/{y}.glb`
- `/api/v1/map/v5/building/{zone}/{uid}.glb`
- raw `.bmd` endpoints also exist when Apicula is absent

3D GLB conversion uses the same expected converter location already used by v4:

```text
runtime/tools/apicula/apicula.exe
```

If Apicula is missing, v5 does **not** draw substitute boxes. The raw original assets and 2D evidence remain available.

## Evidence levels

- `verified`: multiple independent runtime/static relations agree.
- `probable`: exact matrix/chunk identity is strong but a secondary relation is missing.
- `candidate`: a plausible relation exists but is not sufficiently cross-validated.
- `unresolved`: no claim is made.

`DoorUID → Warp → destination` is intentionally not hard-coded as fact. `reindex_dumps_v5.py` surfaces transitions so the relation can be promoted only after it repeats across independent building/center transitions.
