import unittest

from backend.black2.decoders.dialogue_object_resolver import resolve_from_ram
from backend.black2.decoders.visible_text_ledger import VisibleTextLedger

BASE = 0x02000000


def put_u32(ram, addr, value):
    off = addr - BASE
    ram[off:off + 4] = int(value).to_bytes(4, "little")


def put_u16(ram, addr, value):
    off = addr - BASE
    ram[off:off + 2] = int(value).to_bytes(2, "little")


class TestDialogueRuntimeLocatorV10(unittest.TestCase):
    def test_saved_ram_chain_resolves_without_fixed_tcbl(self):
        ram = bytearray(0x400000)
        work_tag = 0x02247574
        work = work_tag + 0x18
        strbuf = 0x022490A4
        parent = 0x0223DBE4
        talk = 0x02321DDC
        tcbl = 0x02324240
        bmpwin = 0x02323280
        bitmap = 0x023232BC
        pixels = 0x023232F4
        ram[work_tag-BASE:work_tag-BASE+13] = b"script_work.c"
        put_u32(ram, work + 0x08, parent)
        put_u32(ram, work + 0x30, strbuf)
        ram[talk-BASE:talk-BASE+12] = b"talkmsgwin.c"
        put_u32(ram, talk + 0x9C, tcbl + 0x18)
        put_u32(ram, talk + 0xA0, bmpwin)
        put_u32(ram, talk + 0xA8, strbuf)
        ram[tcbl-BASE:tcbl-BASE+6] = b"tcbl.c"
        put_u32(ram, tcbl + 0x18, 1)
        put_u32(ram, tcbl + 0x1C, 1)
        put_u32(ram, tcbl + 0x20, bmpwin)
        put_u32(ram, tcbl + 0x24, bitmap)
        put_u32(ram, tcbl + 0x2C, strbuf + 0x32)
        put_u32(ram, bitmap, pixels)
        put_u32(ram, bitmap + 4, (32 << 16) | 240)
        result = resolve_from_ram(bytes(ram))
        self.assertTrue(result.valid)
        self.assertEqual(result.talkmsgwin_addr, talk)
        self.assertEqual(result.tcbl_addr, tcbl)
        self.assertEqual(result.strbuf_addr, strbuf)
        self.assertEqual(result.parent_actor_addr, parent)
        self.assertEqual(result.pixeldata_addr, pixels)

    def test_wait_page_does_not_leak_next_page(self):
        base = 0x022490A0
        raw = bytearray(0x100)
        # Header occupies 0x0c bytes for the ledger view. Two lines followed by
        # CLEAR+LF, then a next-page line which source cursor has pre-read.
        words = [ord(c) for c in "第一行"] + [0xFFFE] + [ord(c) for c in "第二行"]
        words += [0xF000, 0xBE01, 0, 0xFFFE]
        next_page_addr = base + 0x0C + len(words) * 2
        words += [ord(c) for c in "第三行"] + [0xFFFF]
        for i, word in enumerate(words):
            raw[0x0C+i*2:0x0E+i*2] = word.to_bytes(2, "little")
        snap = VisibleTextLedger(base).resolve_visible_text(
            bytes(raw), source_cursor=next_page_addr, phase=1, first_page_latch=1
        )
        self.assertEqual(snap.lines, ["第一行", "第二行"])
        self.assertNotIn("第三行", snap.text)


if __name__ == "__main__":
    unittest.main()
