import pytest
from backend.black2.memory.profiles import get_profile, IREJ1_PROFILE


def test_irej1_profile_offsets():
    profile = get_profile("IREJ")
    assert profile.rom_code == "IREJ"
    assert profile.arm9_base == 0x02000000
    assert profile.known_pointers["game_data"] == 0x0223B330

    # Address translation
    offset = profile.to_offset(0x0223B330)
    assert offset == 0x23B330
    assert profile.to_arm9(offset) == 0x0223B330

    with pytest.raises(ValueError):
        profile.to_offset(0x01FFFFFF)  # Outside ARM9 RAM


def test_memory_range_contains():
    profile = IREJ1_PROFILE
    game_data = profile.ranges["game_data_chain"]
    assert game_data.contains(0x0223B330)
    assert game_data.contains(0x0223B330 + 511)
    assert not game_data.contains(0x0223B330 + 512)
