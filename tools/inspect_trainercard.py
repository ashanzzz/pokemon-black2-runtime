import urllib.request, json, struct

def read_ram(address, size=256):
    offset = address - 0x02000000
    resp = urllib.request.urlopen(f"http://localhost:8765/api/dev/dump_region?offset=0x{offset:X}&length=0x{size:X}")
    d = json.load(resp)
    return bytes.fromhex(d["hex"])

data = read_ram(0x02226924, 0xF0)
print("TrainerCard raw hex:")
for row in range(0, len(data), 16):
    print(f"  +0x{row:02X}: {data[row:row+16].hex()}  {[struct.unpack_from('<I', data, row+j*4)[0] for j in range(4) if row+j*4+4 <= len(data)]}")

# Now let's check cash
# In swan IRDO.yml:
# - Address: 0x200C9BD: getCash
# - Address: 0x200C9C1: addCashToTotal
# Let's read disassembly of getCash at 0x200C9BC
