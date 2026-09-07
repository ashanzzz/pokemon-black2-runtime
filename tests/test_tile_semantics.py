"""Regression coverage for the Gen-5 record layout and movement predicates."""
import struct

import pytest

from backend.black2.world.gen5_rom_map import Gen5MapFormatError, MapChunk
from backend.black2.world.tile_semantics import (
    decode_chunk_terrain,
    decode_terrain_layer,
    decode_tile_semantics,
    tile_semantics_catalog,
)


def terrain_blob(width, height, records, corners=()):
    return struct.pack("<HH", width, height) + b"".join(struct.pack("<4H", *r) for r in (*records, *corners))


def chunk(magic, files):
    return MapChunk(42, magic, b"BMD0", tuple(files), b"\0\0\0\0", ())


def test_terrain_is_interleaved_records_and_not_contiguous_planes():
    raw = terrain_blob(2, 2, [(0, 3, 4, 0x24), (1, 0, 1, 0x81), (4, 14, 0x72, 1), (0, 3, 0x3F, 0x16)])
    layer = decode_terrain_layer(raw)
    grass = layer.tile(0, 0)
    wall = layer.tile(1, 0)
    water = layer.tile(1, 1)
    assert grass["material"]["kind"] == "tall_grass"
    assert grass["raw"] == {"tile_class": 4, "flags": 0x24, "packed_tile_type": 0x240004}
    assert wall["collision"]["static_blocked"] is True
    assert wall["record_offset"] == 12
    assert water["material"]["kind"] == "water"
    assert water["collision"]["requires"] == ["surf"]
    assert water["collision"]["can_walk"] is None
    assert layer.tile(0, 1)["collision"]["ledge_direction"] == "right"


def test_variable_length_corner_table_is_preserved():
    raw = terrain_blob(1, 1, [(2, 1, 3, 0x8000)], [(4, 9, 12, 18), (9, 10, 20, 30)])
    tile = decode_terrain_layer(raw).tile(0, 0)
    assert tile["height"]["mode"] == "corner"
    assert tile["height"]["corner"] == {"slope_1": 2, "slope_1_low_bits": 1, "slope_2": 10, "height_1": 20, "height_2": 30}
    assert tile["height"]["gpos_y"] is None
    assert tile["height"]["world_y"] is None


def test_height_table_sampling_distinguishes_surface_from_runtime_origin():
    layer = decode_terrain_layer(terrain_blob(1, 1, [(0, 51, 3, 0x80)]))
    assert layer.sample_height(0, 0) == pytest.approx(16.003666829506596)
    assert layer.tile(0, 0)["height"]["world_y"] is None
    assert layer.tile(0, 0, chunk_y=32)["height"]["world_y"] == pytest.approx(48.003666829506596)
    assert layer.tile(0, 0, chunk_y=32)["height"]["gpos_y"] is None
    flat = decode_terrain_layer(terrain_blob(1, 1, [(1, 0, 3, 0)]))
    assert flat.sample_height(0, 0) == 0


def test_corner_height_sampling_uses_triangle_and_flip_flag():
    corners = [(0, 0, 3, 51)]
    plain = decode_terrain_layer(terrain_blob(1, 1, [(2, 0, 3, 0)], corners))
    flipped = decode_terrain_layer(terrain_blob(1, 1, [(2, 0, 3, 0x8000)], corners))
    assert plain.sample_height(0, 0, sub_x=4, sub_z=4) == 0
    assert plain.sample_height(0, 0, sub_x=12, sub_z=12) > 16
    assert flipped.sample_height(0, 0, sub_x=12, sub_z=4) == 0
    assert flipped.sample_height(0, 0, sub_x=4, sub_z=12) > 16


def test_missing_height_indices_stay_unknown():
    layer = decode_terrain_layer(terrain_blob(1, 1, [(0, 0xFFFF, 3, 0)]))
    assert layer.sample_height(0, 0) is None
    assert layer.tile(0, 0, chunk_y=0)["height"]["world_y"] is None


def test_gc_contains_two_separate_grids_with_independent_coordinates():
    low = terrain_blob(1, 1, [(0, 3, 4, 0x24)])
    high = terrain_blob(2, 1, [(0, 12, 1, 0x81), (0, 12, 3, 0x80)])
    layers = decode_chunk_terrain(chunk("GC", [low, high]))
    assert len(layers) == 2
    assert layers[0].tile(0, 0)["material"]["kind"] == "tall_grass"
    assert layers[1].tile(1, 0)["material"]["kind"] == "ground_path"
    assert layers[1].tile(1, 0)["layer_index"] == 1
    assert layers[1].tile(1, 0)["height"]["gpos_y"] is None
    assert layers[1].source_file_index == 2


@pytest.mark.parametrize("magic", ["NG", "RD", "XX"])
def test_other_chunk_formats_are_not_guessed(magic):
    raw = terrain_blob(1, 1, [(0, 3, 1, 0x81)])
    assert decode_chunk_terrain(chunk(magic, [raw])) == ()


@pytest.mark.parametrize("raw", [b"", b"\x20\x00", terrain_blob(0, 1, []), terrain_blob(2, 1, [(0, 3, 4, 0)]), terrain_blob(1, 1, [(3, 0, 4, 0)]), terrain_blob(1, 1, [(2, 1, 4, 0)], [(0, 0, 0, 0)])])
def test_malformed_records_and_missing_corners_fail_closed(raw):
    with pytest.raises(Gen5MapFormatError):
        decode_terrain_layer(raw)


def test_missing_gc_layer_is_not_silently_dropped():
    with pytest.raises(Gen5MapFormatError):
        decode_chunk_terrain(chunk("GC", [terrain_blob(1, 1, [(0, 3, 4, 0)])]))


@pytest.mark.parametrize("coordinates", [(-1, 0), (1, 0), (0, -1), (0, 1)])
def test_tile_coordinates_cannot_wrap(coordinates):
    layer = decode_terrain_layer(terrain_blob(1, 1, [(0, 3, 4, 0)]))
    with pytest.raises(IndexError):
        layer.tile(*coordinates)


def test_collision_flag_is_independent_from_visual_material():
    assert decode_tile_semantics(4, 0x25)["collision"]["static_blocked"] is True
    clear = decode_tile_semantics(1, 0x80)
    assert clear["collision"]["static_blocked"] is False
    assert clear["collision"]["can_walk"] is None
    assert decode_tile_semantics(0xFF, 0)["collision"]["can_walk"] is False
    assert decode_tile_semantics(0xFFFF, 0xFFFF)["collision"]["valid_tile"] is False


@pytest.mark.parametrize("tile_class,encounter", [(4, "single"), (5, "single"), (0x21, "single"), (8, "single"), (6, "double"), (7, "double"), (0x22, "double"), (9, "double")])
def test_known_grass_predicates(tile_class, encounter):
    tile = decode_tile_semantics(tile_class, 0)
    assert tile["encounter"] == encounter
    assert tile["material"]["status"] == "verified"


def test_interaction_and_unknown_semantics_have_separate_truth():
    assert decode_tile_semantics(0xD6, 1)["interaction"] == "pc"
    assert decode_tile_semantics(0xE2, 1)["interaction"] == "vending_machine"
    assert decode_tile_semantics(0x24, 0)["interaction"] == "hidden_grotto"
    unknown = decode_tile_semantics(0x1234, 0)
    assert unknown["material"]["kind"] == "unknown"
    assert unknown["material"]["status"] == "unverified"
    assert unknown["collision"]["can_walk"] is None


@pytest.mark.parametrize("tile_class,flags", [(True, 0), (-1, 0), (65536, 0), (0, 65536), (1.0, 0)])
def test_semantics_requires_complete_u16_fields(tile_class, flags):
    with pytest.raises(ValueError):
        decode_tile_semantics(tile_class, flags)


def test_returned_catalog_and_tile_metadata_are_not_mutable_shared_state():
    catalog = tile_semantics_catalog()
    assert len(catalog["materials"]) >= 50
    assert catalog["provenance"]["code"]["rom_code"] == "IREJ"
    barrier = next(row for row in catalog["materials"] if row["tile_class"] == 0x55)
    barrier["blocked_directions"].append("down")
    assert decode_tile_semantics(0x55, 0)["collision"]["blocked_directions"] == ["up", "right"]
