"""Gen-5 terrain records and source-backed gameplay classifications.

Terrain files are row-major arrays of 8-byte records, NOT byte planes.  WB
contains one terrain file, GC two separate terrain files.  A record stores
u16 height-mode/slope, u16 height-table index, u16 class, u16 flags.  The
optional corner table follows the records.  Height indices are not GPos.y.

Format: CTRMapV 3c2778095867f3007ad48d2c268feb0331d43d70, VMapTerrain.java.
Names: SWAN 4324f73a7659353a21bf4c523905c5d09cf6a066, IRDO.yml MapTile_*.
Predicates were cross-checked against the supplied IREJ ROM, overlay 36,
SHA256 2f5353503574a0c4ef88275e91e5b40707411e7d44aa8f1414151500b7a4497b.
Its contiguous MapTile predicate block is 0x021A22F4..0x021A271F (the IRDO
symbol addresses for this block are 0x738 higher).  No RAM address is used.
Additional visual names come from Pokemon DS Map Studio's BW byte-4 legend
at ac30b653e5b090ce116278ed6ba9758fff956673 and remain probable.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any

from .gen5_rom_map import Gen5MapFormatError, MapChunk


FORMAT_SOURCE = (
    "https://github.com/ds-pokemon-hacking/CTRMapV/blob/"
    "3c2778095867f3007ad48d2c268feb0331d43d70/"
    "src/ctrmap/formats/pokemon/gen5/terrain/VMapTerrain.java"
)
SYMBOL_SOURCE = (
    "https://github.com/ds-pokemon-hacking/swan/blob/"
    "4324f73a7659353a21bf4c523905c5d09cf6a066/IRDO.yml"
)
LEGEND_SOURCE = (
    "https://github.com/Trifindo/Pokemon-DS-Map-Studio/blob/"
    "ac30b653e5b090ce116278ed6ba9758fff956673/"
    "src/main/resources/colors/CollisionsColorsBW.txt"
)
PREDICATE_EVIDENCE = {
    "rom_code": "IREJ",
    "overlay_id": 36,
    "overlay_sha256": "2f5353503574a0c4ef88275e91e5b40707411e7d44aa8f1414151500b7a4497b",
    "symbol_source": SYMBOL_SOURCE,
    "collision_predicate": "0x021A2320: invalid TileType or (Flags & 1) != 0",
    "validity_predicate": "0x021A2304: packed TileType != 0xFFFFFFFF and Class != 0x00FF",
    "grass_predicates": "0x021A2440..0x021A247F: single={4,5,0x21,8}, double={6,7,0x22,9}",
    "ledge_predicates": "0x021A235C..0x021A238B: up=0x74, down=0x75, left=0x73, right=0x72",
    "scope": "static terrain only; actor occupancy, scripts, capabilities and elevation still apply",
}


def _entry(kind: str, label: str, *, status: str = "verified", **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "label": label, "status": status, **extra}


# Known TileClass values, not raw permission-plane bytes or texture IDs.
_CLASSES: dict[int, dict[str, Any]] = {
    0x01: _entry("obstacle", "Collision surface", status="probable"),
    0x03: _entry("ground_path", "Ground path", status="probable"),
    0x04: _entry("tall_grass", "Tall grass", encounter="single"),
    0x05: _entry("tall_grass", "Tall grass variant", encounter="single"),
    0x06: _entry("dark_grass", "Dark tall grass", encounter="double"),
    0x07: _entry("dark_grass", "Dark tall grass variant", encounter="double"),
    0x08: _entry("very_tall_grass", "Very tall grass", encounter="single", blocks_cycling=True),
    0x09: _entry("very_tall_grass", "Very tall dark grass", encounter="double", blocks_cycling=True),
    0x0B: _entry("sand", "Sand", status="probable"),
    0x0C: _entry("deep_sand", "Deep sand"),
    0x0E: _entry("snow", "Snow"),
    0x0F: _entry("snow", "Snow without cycling", blocks_cycling=True),
    0x10: _entry("swamp", "Swamp"),
    0x14: _entry("puddle", "Puddle", status="probable"),
    0x1D: _entry("strength_hole", "Strength boulder hole", interaction="strength_hole"),
    0x1F: _entry("grass_path", "Grass path", status="probable"),
    0x21: _entry("tall_grass", "Tall grass variant", encounter="single"),
    0x22: _entry("dark_grass", "Dark tall grass variant", encounter="double"),
    0x24: _entry("hidden_grotto_entrance", "Hidden Grotto entrance grass", interaction="hidden_grotto", blocks_cycling=True),
    0x30: _entry("electric_field", "Electric field"),
    0x32: _entry("electric_rock", "Electric rock"),
    0x3D: _entry("water", "Lake water", status="probable", requires="surf"),
    0x3F: _entry("water", "Water", status="probable", requires="surf"),
    0x41: _entry("water_edge", "Lake shore", interaction="surf_edge"),
    0x44: _entry("water_edge", "Water shore", interaction="surf_edge"),
    0x51: _entry("directional_barrier", "Barrier right", status="probable", blocked_directions=["right"]),
    0x52: _entry("directional_barrier", "Barrier left", status="probable", blocked_directions=["left"]),
    0x53: _entry("directional_barrier", "Barrier up", status="probable", blocked_directions=["up"]),
    0x54: _entry("directional_barrier", "Barrier down", status="probable", blocked_directions=["down"]),
    0x55: _entry("directional_barrier", "Barrier up/right", status="probable", blocked_directions=["up", "right"]),
    0x56: _entry("directional_barrier", "Barrier up/left", status="probable", blocked_directions=["up", "left"]),
    0x57: _entry("directional_barrier", "Barrier down/right", status="probable", blocked_directions=["down", "right"]),
    0x58: _entry("directional_barrier", "Barrier down/left", status="probable", blocked_directions=["down", "left"]),
    0x72: _entry("ledge", "Ledge to right", ledge_direction="right"),
    0x73: _entry("ledge", "Ledge to left", ledge_direction="left"),
    0x74: _entry("ledge", "Ledge upward", ledge_direction="up"),
    0x75: _entry("ledge", "Ledge downward", ledge_direction="down"),
    0x7C: _entry("quicksand", "Quicksand", hazard="forced_movement"),
    0xBE: _entry("catwalk", "Catwalk", blocks_cycling=True),
    0xBF: _entry("catwalk_entry", "Catwalk entry", blocks_cycling=True),
    0xD4: _entry("reception_counter", "Reception counter", interaction="counter"),
    0xD6: _entry("pc", "PC terminal", interaction="pc"),
    0xD8: _entry("television", "Television", interaction="inspect"),
    0xD9: _entry("bookcase", "Bookcase A", interaction="inspect"),
    0xDA: _entry("bookcase", "Bookcase B", interaction="inspect"),
    0xDB: _entry("bookcase", "Bookcase C", interaction="inspect"),
    0xDC: _entry("bookcase", "Bookcase D", interaction="inspect"),
    0xDD: _entry("trash_can", "Trash can A", interaction="inspect"),
    0xDE: _entry("trash_can", "Trash can B", interaction="inspect"),
    0xDF: _entry("assortment", "Assortment A", interaction="inspect"),
    0xE0: _entry("assortment", "Assortment B", interaction="inspect"),
    0xE1: _entry("assortment", "Assortment C", interaction="inspect"),
    0xE2: _entry("vending_machine", "Vending machine", interaction="vending_machine"),
}


def decode_tile_semantics(tile_class: int, flags: int) -> dict[str, Any]:
    """Decode a real u16 TileClass/u16 Flags pair, never a plane byte.

    ``can_walk`` is deliberately not true just because static collision is
    clear: water, height, NPCs and scripted restrictions require other inputs.
    """
    if type(tile_class) is not int or type(flags) is not int or not (0 <= tile_class <= 0xFFFF and 0 <= flags <= 0xFFFF):
        raise ValueError("tile_class and flags must be unsigned 16-bit integers")
    found = _CLASSES.get(tile_class)
    material = dict(found) if found else _entry("unknown", "Unknown terrain", status="unverified")
    if "blocked_directions" in material:
        material["blocked_directions"] = list(material["blocked_directions"])
    material.update({
        "tile_class": tile_class,
        "tile_class_hex": f"0x{tile_class:04X}",
        "source": LEGEND_SOURCE if material["status"] == "probable" else SYMBOL_SOURCE if found else None,
        "visual_texture_verified": False,
    })
    valid = tile_class != 0xFF and not (tile_class == 0xFFFF and flags == 0xFFFF)
    blocked = not valid or bool(flags & 1)
    return {
        "material": material,
        "collision": {
            "status": "verified",
            "static_blocked": blocked,
            "valid_tile": valid,
            "can_walk": False if blocked else None,
            "blocked_directions": material.get("blocked_directions", []),
            "ledge_direction": material.get("ledge_direction"),
            "requires": [material["requires"]] if "requires" in material else [],
            "reason": "invalid_tile_type" if not valid else "flags_bit_0" if blocked else "static_flag_clear; dynamic movement and height are unresolved",
            "source": "IREJ overlay 36 MapTile_BlocksCollision, cross-checked with SWAN",
        },
        "interaction": material.get("interaction"),
        "encounter": material.get("encounter"),
        "raw": {"tile_class": tile_class, "flags": flags, "packed_tile_type": tile_class | (flags << 16)},
    }


def tile_semantics_catalog() -> dict[str, Any]:
    """Public material legend with fixed provenance and explicit confidence."""
    return {
        "schema_version": "black2.tile-semantics.v1",
        "encoding": "u16 TileClass + u16 Flags, little-endian, at terrain record +4/+6",
        "materials": [decode_tile_semantics(value, 0)["material"] for value in sorted(_CLASSES)],
        "flags": [{"mask": 1, "meaning": "static_collision", "status": "verified"}],
        "provenance": {"format": FORMAT_SOURCE, "names": SYMBOL_SOURCE, "legend": LEGEND_SOURCE, "code": dict(PREDICATE_EVIDENCE)},
        "limitations": ["No static material guarantees current passability", "Terrain layer index is not GPos.y", "Warp destinations come from zone event records, not material IDs"],
    }


@dataclass(frozen=True)
class TerrainLayer:
    width: int
    height: int
    layer_index: int
    source_file_index: int
    records: tuple[tuple[int, int, int, int], ...]
    corners: tuple[tuple[int, int, int, int], ...]
    trailing_bytes: int

    def tile(self, x: int, z: int, *, chunk_y: float | None = None, chunk_span: float = 512.0) -> dict[str, Any]:
        if not (0 <= x < self.width and 0 <= z < self.height):
            raise IndexError((x, z))
        first, height, tile_class, flags = self.records[z * self.width + x]
        mode = first & 3
        corner = self.corners[height] if mode == 2 else None
        relative_y = self.sample_height(x, z, chunk_span=chunk_span)
        return {
            "x": x, "z": z, "layer_index": self.layer_index,
            "source_file_index": self.source_file_index,
            "record_offset": 4 + (z * self.width + x) * 8,
            "raw_record_hex": struct.pack("<4H", first, height, tile_class, flags).hex(),
            "sampled_tile_type": {"class": tile_class & 0xFFFE, "flags": flags, "normalization": "CTRMapV VMapSamplerUtil clears Class bit 0 when sampling"},
            "height": {
                "mode": ("default", "plain", "corner")[mode],
                "slope_index": first >> 2,
                "height_index": height,
                "world_y": relative_y + chunk_y if chunk_y is not None and relative_y is not None else None,
                "chunk_relative_world_y": relative_y,
                "sample": "tile_center",
                "chunk_world_y": chunk_y,
                "gpos_y": None,
                "corner": ({"slope_1": corner[0] >> 2, "slope_1_low_bits": corner[0] & 3, "slope_2": corner[1], "height_1": corner[2], "height_2": corner[3]} if corner else None),
                "status": "height_sampled" if chunk_y is not None and relative_y is not None else "chunk_height_transform_required" if relative_y is not None else "height_table_index_unresolved",
            },
            **decode_tile_semantics(tile_class, flags),
        }

    def sample_height(self, x: int, z: int, *, sub_x: float = 8.0, sub_z: float = 8.0, chunk_span: float = 512.0) -> float | None:
        """Sample world-unit height relative to the chunk's runtime Y origin.

        This is CTRMapV's terrain sampler with the supplied IREJ height and
        slope tables.  GPos.y must still be obtained from the game, not by
        interpreting the layer number or height-table index as a coordinate.
        """
        if not (0 <= x < self.width and 0 <= z < self.height):
            raise IndexError((x, z))
        if not (0 <= sub_x < 16 and 0 <= sub_z < 16) or chunk_span <= 0:
            raise ValueError("sample offsets must be in [0,16), and chunk_span positive")
        first, height, _tile_class, flags = self.records[z * self.width + x]
        mode, slope = first & 3, first >> 2
        if mode == 1:
            return 0.0
        if mode == 2:
            corner = self.corners[height]
            upper = sub_x > sub_z if flags & 0x8000 else sub_x + sub_z < 16
            slope, height = (corner[0] >> 2, corner[2]) if upper else (corner[1], corner[3])
        if not (height < len(OFFSET_TABLE_FX32) and slope * 3 + 2 < len(SLOPE_TABLE_FX32)):
            return None
        nx, ny, nz = SLOPE_TABLE_FX32[slope * 3:slope * 3 + 3]
        if not ny:
            return None
        rel_x = x * 16 + sub_x - chunk_span / 2
        rel_z = z * 16 + sub_z - chunk_span / 2
        return -(nx * rel_x - nz * rel_z + OFFSET_TABLE_FX32[height]) / ny


def decode_terrain_layer(raw: bytes, *, layer_index: int = 0, source_file_index: int = 1) -> TerrainLayer:
    """Parse a WB/GC terrain subfile, including its variable corner table."""
    if len(raw) < 4:
        raise Gen5MapFormatError("Terrain dimensions are truncated")
    width, height = struct.unpack_from("<HH", raw)
    if not (1 <= width <= 512 and 1 <= height <= 512):
        raise Gen5MapFormatError(f"Invalid terrain dimensions {width}x{height}")
    record_end = 4 + width * height * 8
    if len(raw) < record_end:
        raise Gen5MapFormatError("Terrain record array is truncated")
    records = tuple(struct.iter_unpack("<4H", raw[4:record_end]))
    if any((r[0] & 3) == 3 for r in records):
        raise Gen5MapFormatError("Terrain contains unsupported height mode 3")
    corner_count = max((r[1] + 1 for r in records if (r[0] & 3) == 2), default=0)
    corner_end = record_end + corner_count * 8
    if len(raw) < corner_end:
        raise Gen5MapFormatError("Terrain corner table is truncated")
    corners = tuple(struct.iter_unpack("<4H", raw[record_end:corner_end]))
    return TerrainLayer(width, height, layer_index, source_file_index, records, corners, len(raw) - corner_end)


def decode_chunk_terrain(chunk: MapChunk) -> tuple[TerrainLayer, ...]:
    """Return independent WB/GC terrain layers; NG/RD need other samplers."""
    count = {"WB": 1, "GC": 2}.get(chunk.container_magic, 0)
    if not count:
        return ()
    if len(chunk.auxiliary_files) < count:
        raise Gen5MapFormatError(f"{chunk.container_magic} chunk has fewer than {count} terrain files")
    return tuple(decode_terrain_layer(chunk.auxiliary_files[i], layer_index=i, source_file_index=i + 1) for i in range(count))


# IREJ overlay 36: height offsets at 0x021D2898, slope vectors at 0x021D2128.
# Integer FX32 values below match the ROM byte-for-byte; see module provenance.
OFFSET_TABLE_FX32 = (
    -131039, -98279, 196559, 0, -989413, -897280, -982988, -1023055, -990388, 234411, 205110, 131039, 65519, 324307, -87360, -290751,
    -138988, -305760, -231648, 92659, 229319, -92659, 602285, 98279, 648614, 545159, -277977, 327095, -46329, -463296, 741274, 138988,
    -642476, -256990, -192742, -327599, -578228, -513981, -321238, 321238, -128495, 256990, -64247, 185318, 555955, -324307, 192742, -185318,
    277977, 463296, 146507, -65519, -117205, -58602, -29301, 87904, 293014, -133742, -52532, -106993, 263712, 32759, -90859, -36343,
    -105065, -53496, -26748, -175808, -187239, -160490, -174719, -109031, -54515, -157598, 58602, -140087, -146507, -87554, -152880, -109199,
    -263712, 117205, -205110, 410220, -293014, -917279, -741274, -555955, 615213, 416966, -694944, -648614, 370637, 322315, 351617, 509625,
    641962, 380918, 439521, 240735, -416966, 106993, -370637, 133742, -61305, -114436, -28609, -69614, -81740, -429747, -406293, -384981,
    36783, -57218, 44957, -36783, -4094, 24522, -44957, -49044, 49044, -24522, 12261, 46329, -602285, -527425, -410220, 694944,
    264795, 498124, 802452, 414959, 817738, 960959, -294839, 513981, 457102, -574308, 456158, -601209, 770971, 327599, 515705, 578228,
    449733, 128495, 353260, 390865, 360359, 642476, 459238, 385485, -509625, -509626, 231648, -1048319, -588531, -190691, 381382, -393119,
    -262079, 267484, 310788, -124315, 317818, 175808, -87904, -234411, -646285, -32759, 509626, -2015339, -2015338, -667419, -1945844, 57923,
    -2850119, -562897, -3275999, -3079439, -14520, 41666, -547504, -619746, -31349, 22619, -849741, -1210495, -51083, 60420, -1899514, -1899515,
    -102884, -53225, -137797, -94431, 214199, -90286, -352227, -181846, -147743, -656989, -656990, -239106, -219211, -2285512, -2549224, -1349979,
    -552137, -766505, -1853185, -3210479, -316455, -651915, -1806855, 101296, 435507, -417232, -123066, -1962573, -1635477, -1891183, -1670181, -231291,
    -183695, -1381326, -1533011, 60748, -291935, -1097983, -1703520, -2080401, -2666430, -1365202, -1376422, 17832, -69142, -2032880, -89998, -273733,
    -2578526, -1703519, -1437984, -274671, 67640, -717735, -714813, -389336, -876006, -2519923, -2637129, -1575986, -1287165, -1058508, -946662, -782988,
    -737658, -618277, -604224, -509845, -2373416, -513415, -433381, -2817359, -448155, -376682, -2154328, -2246867, -2607827, -2487603, -399189, -316066,
    -458004, -1900079, -353678, -536490, 355332, -540104, 445381, 589869, 293982, 44132, -867833, -2455470, -332708, -1787387, 313310, -1494373,
    -1699483, -2129399, -735460, -647990, -1117745, 25218, -2223822, -1952635, -2344114, -2300364, -1406468, -633999, -725173, -12849, 108102, -334087,
    -982799, 79454, -720719, -655199, -189139, -205592, -398335, -567419, -498124, -179997, -342167, -374478, -267668, -476728, -545159, -542839,
    -884519, -571837, -450170, -655200, -524400, -540291, -490643, -480479, -732535, -937645, -490306, -468822, -560350, -534968, -879043, -936194,
    -787603, -347729, -393665, -561716, -644631, -680903, 145034, 118530, -286036, -972922, -237060, -766612, -82876, -766611, -605247, -849489,
    -849488, 102796, -539680, -487458, -1657539, -664129, -2754335, -316335, 235224, 243802, 48760, -604064, -2784599, -2751839, 178788, 308816,
    -89946, -544020, -2256210, 113774, 633886, -592650, -745892, -882698, -1113454, -1361807, -1992174, -1550947, -1289263, -1230660, -948241, -794546,
    -668175, -581656, -605838, -2084833, -629066, -650140, -477081, -1389889, -426232, -750540, -478293, -376999, 238364, -587964, 238363, -1257176,
    -966947, -1740087, -1436218, -1204570, -488282, -378279, -85785, -277405, 90187, 186473, -454723, -1019252, -621577, 138285, -375345, 103596,
    -294232, -730935, -290014, -1575207, -1323979, -586028, -1343559, -833933, -832215, -351617, -718731, -243059, -380918, -322315, -271655, -75399,
    -214358, -851759, -1179359, -226967, -193885, -201946, -166786, -183492, -146281, -192667, -157520, -223203, -172342, -264795, -222473, -414855,
    -269350, -643075, 718731, 560350, 730982, 675345, 859530, 539991, 26748, 650878, 556182, 725830, 706724, -1045232, -592428, 570885,
    499551, 593019, -1158240, -1310399, 595209, -673933, -10771, 617857, -818999, 603927, -761837, 454365, 476542, -113483, -196022, -718750,
    -225965, -135281, 766597, -261308, -241030, -261363, 783924, 759146, 783524, 750282, 783011, 739568, 782338, 726367, 781431, 709715,
    780168, 688097, 778337, 658982, 775542, 762764, 745892, 703234, -196559, -458639, -589679, 213987, -963714, -449733, 111236, 82876,
    29301, 113483, -481471, 320981, -439521, 468822, 718109, -625450, 254812, -162153, 162153, -794387, -310788, -695459, -302623, 497261,
    -453935, -994523, -393801, -870208, 372946, 621577, 440131, -23164, 347472, -254813, -254812, -347472, -301142, -629095, 145375, 297686,
    160490, -272579, -436127, 381611, -599675, -163547, 43679, -182501, -28595, -186473, -47849, -808050, 80245, -218063, -243335, -107660,
    -248630, -122790, -251996, -881986, 262079, -21195, -127919, -15775, -127127, -8916, -125998, 11962, -121667, 28595, 52532, 87359,
    -1023356, -255839, -141978, -254254, -486461, -532790, -69494, 69494, -163799, 301142, 254813, 23164, 671779, 486461, -115824, -698879,
    563331, -472471, -926592, -880263, -267484, -401226, -1065581, -229319, -703234, -360359, -899466, -385485, -835219, -770971, 154432, -401523,
    64247, -340451, 302623, 644631, -427974, -372946, 481471, -497261, -508220, 416107, 761837, 870208, 347729, 123545, 340451, 683735,
    534968, 561716, 435104, 37827, 187238, 378279, 248630, -374477, 748955, -756559, 605247, 226967, 775704, -962943, -829201, -820440,
    187239, 586028, -641962, -113484, -416107, -37827, 77216, 79020, 189139, 207192, 75655, 256815, 262534, 963714, -346905, 508220,
    556727, 199891, -672363, 614798, 688583, -70043, -126093, -53857, -700142, -622439, 124315, 732535, 458639, 393119, 491763, -151311,
    -492618, -454365, -476542, -612883, -651864, 216204, 109031, 174719, 721685, 584581, 504373, -355193, -365491, -355590, -165753, -177795,
    -706724, 673933, 254254, -556727, 567419, 294839, -371279, -414959, -654191, 454299, -718109, 453935, -763223, -437773, 54515, -546000,
    -327095, -349439, -440131, -208483, -708707, 115824, 273733, -563331, 208483, 532790, 349439, 527425, -175109, -181719, 240239, 526987,
    799567, 786239, 799566, -454299, -508815, 787603, 854082, -109200, 833933, 625450, 163547, 654191, -381611, -579120, 764438, 393801,
    -524159, 615330, -1027962, -790839, 121667, 608337, 163799, 879043, -1343159, -1146599, -1318564, -1212119, -1244879, -1054851, -636019, -840525,
    -787993, -690535, -1026479, -643393, -401503, -1230427, -764438, -1203679, -996248, -950039, -581503, -982800, -908344, -671779, -683735, -559419,
    -802452, -809955, -762764, 190691, -803047, -775542, 151311, 193885, -754641, -778337, 201946, 194584, -752320, -780168, 211016, 195042,
    -750040, -781431, 217753, 195357, -586073, -629990, 62999, 62157, 490452, -645961, 299056, -358867, 579120, 680903, -320981, 401226,
    -80245, -240735, -907871, -651192, 260477, 455834, -131040, -213987, -612405, -668294, -680239, 335835, 339750, 258514, 340119, 259445,
    -57190, 502320, -872254, -87359, 599675, 218400, -19755, 40547, -710913, -240239, -502319, -43679, 131040, -525328, -844825, -780079,
    -68370, -889892, -922858, -895436, -899195, -750690, -828832, -899986, -971973, 658862, -900340, -313601, -717838, 452399, -969427, -1000834,
    350748, 323142, 296325, -389168, -843199, -797155, -840170, -770445, -826328, -722207, 142976, 95345, 57190, -775704, -588465, 529591,
    790839, 210131, 218063, 192620, 557607, 730005, 669171, -304168, 72517, -51798, 73253, -43952, 893693, 60833, -365002, -72517,
    -196832, 797690, -161157, -300429, 134675, -73253, -190459, 491399, -278363, 131856, -131856, 747186, -321148, 425836, -669171, -445463,
    -10359, -366267, 43952, -673375, 321148, 556919, 981598, -249062, 622439, 864392, -735533, 922995, 1217053, -569779, -551595, -610352,
    -516573, -424870, 1088746, 486670, 147116, -395569, -362586, -341463, -224866, -464040, -383306, -219760, 10359, -238271, 196832, -425836,
    182501, -486670, -778655, -187238, -507817, -659282, -457524, -307665, 258990, 445463, 179433, -611217, 446529, 481857, 133834, 214464,
    85785, -102555, 288930, 483473, -11962, -113955, 236397, 520405, -113821, -122070, -186318, 586616, 859848, 243335, -379061, -393158,
    -85330, -370830, -475415, 109711, 502414, -283709, -670457, 304753, 510677, 810768, -645962, -486902, 949757, 928080, 661989, 999458,
    945591, 699817, -287094, -14650, -681548, -756252, -182735, 289114, 805789, 762330, 176113, -54051, 425879, -214527, -735581, -148162,
    -26266, -600679, -306202, -621638, -779237, -176113, -571378, -60833, 304168, 365002, 413164, 290751, -973340, 63563, -435104, 294232,
    134920, 1042416, 472849, 1191581, 903427, -443308, -393996, -529012, -483473, -428929, 851673, -851673, 435021, 334355, 341463, -40122,
    -131584, 414600, 507621, -120367, 200613, -128678, 219760, 245153, 181719, -353236, -91557, -308923, -479328, 102328, -357440, 454172,
    328845, -673213, 296214, -757774, 107179, -132397, -542076, -283919, -608337, -314547, -185869, -239245, -203358, 400333, 336966, -614798,
    -776488, -543310, -300250, 243059, -857858, -1441439, -850881, -196357, -130904, -161873, -96501, -196337, -163392, -130891, -98035, -196314,
    588944, -130876, -588864, 130858, -602383, -588772, -606854, -588662, -392441, -1019251, -392576, -850582, -834627, 523435, 537064, 261717,
    246759, 241973, 261676, 261627, -850448, -837305, 523352, 533878, -850290, 130813, 523255, -195584, 391169, -251711, -195357, -266143,
    -195042, -130389, -186985, -201623, -130238, -130028, 130784, 165251, 130749, 162970, 130706, 392120, -653924, -457746, -421825, -423723,
    -457624, -457474, 457289, -130654, 455487, 457055, 448988, 456756, -130501, -783924, -780836, -785729, -783524, -783011, 695459, 820440,
    427974, 374478, 559419, 588465, -870043, -75655, 932365, -1096685, -1016440, 832215, 936194, -1040224, -910196, -1021355, -1098119, -342744,
    455823, 228496, 585126, 260056, -470695, -716275, -904799, 387771, -452399, -706122, -586842, -635637, -62157, -529591, -668710, -15890,
    -197550, -20719, 748956, 994523, -491763, -407700, -350748, -409314, -353061, -411184, -413164, -95345, 95658, 88265, 43085, -434610,
    -290069, -129257, -258514, 12609, 61772, 580029, 617728, 671670, 444946, -748956, 508509, 770445, 53496, 663015, -474120, -1069937,
    889892, 1007506, 989691, 849489, 870043, -571398, -556182, 794387, 600542, -1017019, -596006, -1127354, -1011222, -1108200, -622674, -237793,
    -493157, 547504, 6794, -508509, -572073, 636019, 745051, 690535, -945699, -1113839, -786239, -1111911, -1172667, -791138, 655199, -556919,
    589679, 1244879, 1212119, 1343159, 1113839, 643075, 687959, -517028, -466545, 197550, -3013919, -3931199, -1145791, -1109620, -141344, -243647,
    244141, -828769, -745272, -102796, -43680, -21840, 272579, -425879, -491399, 710913, 693874, -633359, 794546, 612405, -615213, 436799,
    756559, 835219, 899466, 657004, -985507, -460974, -1128256, -926770, 175109, -615330, 227282, 1277639, -32123, -94569, -289114, 96371,
    -81960, -818410, -254110, 161157, -435021,
)
SLOPE_TABLE_FX32 = (
    0, 4094, 0, -1831, 3662, 0, 4015, 803, 0, 3974, 883, -441, 3996, 799, 399, 4028,
    732, 0, 0, 3662, 1831, 1831, 3662, 0, 0, 2895, -2895, -2729, 2729, -1364, -3407, 2271,
    0, -2895, 2895, 0, -2729, 2729, 1364, 3407, 2271, 0, 0, 2895, 2895, 0, 2271, -3407,
    2895, 2895, 0, 0, 803, 4015, -4015, 803, 0, 0, 803, -4015, 0, 3662, -1831, -1671,
    3343, 1671, -3283, 2188, -1094, 1671, 3343, 1671, 3283, 2188, -1094, -3283, 2188, 1094, -1671, 3343,
    -1671, 3283, 2188, 1094, 1671, 3343, -1671, 2729, 2729, 1364, -3662, 1831, 0, 3662, 1831, 0,
    0, 1831, -3662, 0, 1831, 3662, 2729, 2729, -1364, 1364, 2729, 2729, 0, 4087, 255, -255,
    4087, 0, 255, 4087, 0, 0, 4087, -255, -186, 2984, -2797, 0, 2987, -2800, 186, 2984,
    -2797, -1671, 1671, -3343, 2364, 2364, 2364, 0, 2271, 3407, -1364, 2729, 2729, 0, 732, -4028,
    4012, 729, 364, -4028, 732, 0, 3997, 888, 0, 3974, 883, 441, 0, 255, -4087, 3972,
    993, 0, -3972, 993, 0, 0, 1294, -3884, 3884, 1294, 0, 0, 993, -3972, -965, 965,
    -3860, -4039, 673, 0, 2979, 1986, -1986, 4085, 281, 0, 4072, 301, -301, -4047, 622, 0,
    4083, 302, 0, 4069, 325, -325, 0, 1520, -3802, 4081, 326, 0, 4064, 353, -353, 2364,
    2364, -2364, 4079, 354, 0, 3986, 419, -839, 4072, 428, 0, 4039, 475, -475, 4066, 478,
    0, 4024, 536, -536, 0, 622, -4047, -3038, 868, -2604, 4059, 541, 0, 4001, 615, -615,
    -3937, 1124, 0, 4047, 622, 0, 3965, 721, -721, 0, 1124, 3937, -3943, 985, -492, -3704,
    1234, -1234, 0, 452, -4069, 4032, 504, -504, -2173, 620, -3414, 1094, 2188, 3283, 441, 883,
    -3974, -3854, 513, 1284, 0, 1520, 3802, 3796, 1084, -1084, 2838, 810, 2838, -1364, 2729, -2729,
    -4050, 540, 270, -3985, 664, -664, 3900, 1114, -557, 3704, 1234, -1234, 3937, 1124, 0, 3564,
    1425, -1425, 1364, 2729, -2729, 0, 673, -4039, 2818, 939, -2818, 747, 1495, -3738, 3802, 1520,
    0, 3906, 868, -868, 0, 888, 3997, 0, 732, 4028, 1400, 933, 3733, 0, 993, 3972,
    3940, 788, -788, 3738, 1495, 747, 3860, 965, -965, -788, 788, -3940, -1425, 1425, 3564, -2786,
    1114, 2786, 1986, 1986, -2979, 1094, 2188, -3283, -3802, 1520, 0, 3343, 1671, 1671, 3343, 1671,
    -1671, 0, 1294, 3884, 1234, 1234, 3704, -3884, 1294, 0, -3808, 476, 1428, -4055, 506, 253,
    253, 506, -4055, 0, 507, -4063, -4063, 507, 0, -2364, 2364, -2364, -3860, 965, -965, -3940,
    788, -788, 504, 504, -4032, -4013, 573, -573, -4053, 579, 0, 0, 579, -4053, -3343, 1671,
    -1671, -3574, 893, -1787, -2818, 939, -2818, 1234, 1234, -3704, -3704, 1234, 1234, -2979, 1986, -1986,
    639, 1279, -3837, -639, 1279, -3837, -3900, 1114, -557, 0, 314, 4082, -4082, 314, 0, 0,
    673, 4039, -3985, 664, 664, 0, 302, -4083, 156, 313, -4079, -313, 313, -4070, -4070, 313,
    -313, 0, 314, -4082, -156, 313, -4079, -4066, 338, -338, -4080, 340, 0, -4061, 369, -369,
    -4078, 370, 0, -4054, 405, -405, -4074, 407, 0, -4045, 449, -449, -4069, 452, 0, -4032,
    504, -504, -1234, 1234, -3704, -3343, 1671, 1671, 1671, 1671, 3343, 1671, 1671, -3343, -893, 1787,
    3574, -1094, 2188, 3283, -747, 1495, 3738, -1094, 2188, -3283, -893, 1787, -3574, -747, 1495, -3738,
    0, 1124, -3937, 441, 883, 3974, 492, 985, 3943, 557, 1114, 3900, 639, 1279, 3837, 747,
    1495, 3738, 893, 1787, 3574, -3997, 888, 0, 0, 888, -3997, 492, 985, -3943, 557, 1114,
    -3900, 893, 1787, -3574, 965, 965, -3860, -2364, 2364, 2364, -1671, 1671, 3343, -3738, 747, 1495,
    3574, 1787, -893, 965, 965, 3860, -3965, 721, -721, -664, 664, 3985, 4039, 673, 0, 1425,
    1425, -3564, 3704, 1234, 1234, -3860, 965, 965, 3860, 965, 965, -3940, 788, 788, 3940, 788,
    788, 3985, 664, 664, 4053, 579, 0, -4013, 573, 573, 4013, 573, 573, 4063, 507, 0,
    -4032, 504, 504, 4032, 504, 504, 4069, 452, 0, -3738, 1495, 747, 0, 452, 4069, -1234,
    1234, 3704, -965, 965, 3860, -788, 788, 3940, 0, 579, 4053, -664, 664, -3985, 3574, 1787,
    893, -492, 985, 3943, -1267, 844, 3801, 3996, 799, -399, 3052, 1220, 2441, -492, 985, -3943,
    1084, 1084, -3796, -3921, 653, 980, 3733, 933, 1400, 3574, 893, -1787, 664, 664, 3985, 788,
    788, 3940, 664, 664, -3985, 788, 788, -3940, 3574, 893, 1787, -3574, 1787, -893, 2188, 1094,
    3283, 3283, 1094, 2188, -2979, 1986, 1986, 3738, 1495, -747, 3283, 1094, -2188, 2188, 1094, -3283,
    -2188, 1094, -3283, -2076, 692, -3460, -3460, 692, 2076, -3283, 1094, 2188, -2188, 1094, 3283, 2979,
    1986, 1986, 0, 185, 4090, -4090, 185, 0, 4090, 185, 0, -4085, 194, -194, 4085, 194,
    -194, -4090, 194, 0, -4084, 204, -204, 4090, 194, 0, 4084, 204, -204, -4089, 204, 0,
    0, 204, -4089, 4089, 204, 0, 0, 215, 4089, -4089, 215, 0, 226, 226, 4082, 0,
    227, 4088, 240, 240, 4080, 0, 240, 4087, 4087, 240, 0, -4082, 226, -226, 4089, 215,
    0, 4082, 226, -226, 0, 215, -4089, 226, 226, -4082, 240, 240, -4080, 0, 227, -4088,
    0, 240, -4087, -4088, 227, 0, -4080, 240, -240, 4088, 227, 0, 4080, 240, -240, -4087,
    240, 0, 0, 407, 4074, 449, 449, 4045, 504, 504, 4032, 0, 507, 4063, 0, 407,
    -4074, 449, 449, -4045, 0, 255, 4087, 271, 271, 4076, 0, 272, 4085, 291, 291, 4074,
    0, 291, 4084, 4084, 291, 0, -4087, 255, 0, 271, 271, -4076, 291, 291, -4074, 0,
    272, -4085, 0, 291, -4084, 338, 338, 4066, 0, 340, 4080, 369, 369, 4061, 0, 370,
    4078, 4078, 370, 0, 338, 338, -4066, 369, 369, -4061, 0, 340, -4080, 0, 370, -4078,
    3808, 476, 1428, 3808, 476, -1428, 1279, 639, 3837, -3837, 639, -1279, 2729, 1364, -2729, 2729,
    1364, 2729, -441, 883, 3974, -3783, 582, -1455, -3783, 582, 1455, 2123, 849, -3397, -3397, 849,
    2123, -3283, 1094, -2188, 2619, 1746, 2619,
)
