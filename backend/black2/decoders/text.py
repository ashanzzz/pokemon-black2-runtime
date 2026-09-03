"""Pokémon Gen 5 (Black 2 / White 2) Accurate Text Decoder & Character Table."""

from typing import List, Tuple, Optional

# Gen 5 standard character table mapping (for Gen 5 NDS games)
GEN5_CHAR_MAP = {
    0x0000: " ",
    0x0101: "0", 0x0102: "1", 0x0103: "2", 0x0104: "3", 0x0105: "4",
    0x0106: "5", 0x0107: "6", 0x0108: "7", 0x0109: "8", 0x010A: "9",
    0x0121: "A", 0x0122: "B", 0x0123: "C", 0x0124: "D", 0x0125: "E",
    0x0126: "F", 0x0127: "G", 0x0128: "H", 0x0129: "I", 0x012A: "J",
    0x012B: "K", 0x012C: "L", 0x012D: "M", 0x012E: "N", 0x012F: "O",
    0x0130: "P", 0x0131: "Q", 0x0132: "R", 0x0133: "S", 0x0134: "T",
    0x0135: "U", 0x0136: "V", 0x0137: "W", 0x0138: "X", 0x0139: "Y",
    0x013A: "Z",
    0x013B: "a", 0x013C: "b", 0x013D: "c", 0x013E: "d", 0x013F: "e",
    0x0140: "f", 0x0141: "g", 0x0142: "h", 0x0143: "i", 0x0144: "j",
    0x0145: "k", 0x0146: "l", 0x0147: "m", 0x0148: "n", 0x0149: "o",
    0x014A: "p", 0x014B: "q", 0x014C: "r", 0x014D: "s", 0x014E: "t",
    0x014F: "u", 0x0150: "v", 0x0151: "w", 0x0152: "x", 0x0153: "y",
    0x0154: "z",
    0x015B: "!", 0x015C: "?", 0x015D: "…", 0x015E: "-", 0x015F: "・",
    0x0160: "/", 0x0161: "「", 0x0162: "」", 0x0163: "『", 0x0164: "』",
    0x0165: "(", 0x0166: ")", 0x0167: "♂", 0x0168: "♀", 0x0169: "+",
    0x016A: "—", 0x016B: "=", 0x016C: "×", 0x016D: ":", 0x016E: ";",
    0x016F: ",", 0x0170: "."
}


def decode_gen5_string(words_or_bytes: List[int], max_length: int = 256) -> Optional[str]:
    """Strictly decode a 16-bit word sequence into text.
    Returns None if the sequence contains invalid characters / raw binary junk.
    """
    if not words_or_bytes:
        return ""

    words: List[int] = []
    if len(words_or_bytes) >= 2 and all(0 <= b <= 255 for b in words_or_bytes) and len(words_or_bytes) % 2 == 0:
        for i in range(0, len(words_or_bytes), 2):
            w = words_or_bytes[i] | (words_or_bytes[i + 1] << 8)
            words.append(w)
    else:
        words = words_or_bytes

    result: List[str] = []
    i = 0
    valid_char_count = 0

    while i < len(words) and len(result) < max_length:
        code = words[i]

        # 0xFFFF: Gen 5 string terminator
        if code == 0xFFFF or code == 0x0000 and i > 0 and words[i-1] == 0:
            break

        # 0xFFFE: Control code sequence
        if code == 0xFFFE:
            if i + 1 < len(words):
                ctrl_type = words[i + 1]
                ctrl_len = words[i + 2] if i + 2 < len(words) else 0
                if ctrl_type == 0x0001:
                    result.append("\n")
                elif ctrl_type == 0x0002:
                    result.append("\n[PAGE]\n")
                elif ctrl_type == 0x0003:
                    result.append("{VAR}")
                i += max(2, 2 + ctrl_len)
                continue
            else:
                break

        # Gen 5 specific character table
        if code in GEN5_CHAR_MAP:
            result.append(GEN5_CHAR_MAP[code])
            valid_char_count += 1
        # Standard readable ASCII / Chinese Unicode (used in Chinese translation ROMs)
        elif (0x0020 <= code <= 0x007E) or (0x4E00 <= code <= 0x9FA5) or (0x3000 <= code <= 0x303F) or (0x3040 <= code <= 0x30FF) or (0xFF00 <= code <= 0xFF5E):
            result.append(chr(code))
            valid_char_count += 1
        else:
            # If encountering invalid / non-text binary words (e.g. ARM instructions / pointers), reject string
            return None

        i += 1

    text = "".join(result).strip()
    # Require at least 1 valid char and no garbled chars
    if valid_char_count >= 1:
        return text
    return None


def extract_printable_strings(bytes_data: List[int], min_len: int = 2) -> List[Tuple[int, str]]:
    """Scan memory bytes and extract ONLY genuinely valid text strings."""
    found: List[Tuple[int, str]] = []
    i = 0
    while i <= len(bytes_data) - (min_len * 2):
        words = []
        start_idx = i
        while i + 1 < len(bytes_data):
            w = bytes_data[i] | (bytes_data[i + 1] << 8)
            if w == 0xFFFF:
                break
            # Check if code is valid in Gen 5 or Unicode
            if (w in GEN5_CHAR_MAP) or (0x0020 <= w <= 0x007E) or (0x4E00 <= w <= 0x9FA5) or (0x3000 <= w <= 0x30FF) or w == 0xFFFE:
                words.append(w)
                i += 2
            else:
                break

        if len(words) >= min_len:
            s = decode_gen5_string(words)
            if s and len(s) >= min_len:
                found.append((start_idx, s))

        i = start_idx + 2

    return found
