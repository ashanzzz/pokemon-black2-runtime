import urllib.request, json, binascii, time

def read_mem(offset, length):
    url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
    body = {'ranges': [{'id': 'm', 'domain': 'Main RAM', 'offset': offset, 'length': length}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return binascii.unhexlify(data['results']['m']['hex'])

def get_snapshot():
    # 0x0223B680 (state, 0x80 bytes), 0x0223DCE0 (actor, 0x80 bytes), 0x023329A0 (core, 0x80 bytes)
    return {
        "state": read_mem(0x23B680, 0x80),
        "actor": read_mem(0x23DCE0, 0x80),
        "core": read_mem(0x3329A0, 0x80),
    }

snap_foot = get_snapshot()

# Mount bicycle
req = urllib.request.Request('http://127.0.0.1:8765/api/v1/player/bicycle/mount', data=b'{}', headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
print("Mount result:", res.get("status"))

time.sleep(0.5)
snap_bike = get_snapshot()

# Dismount back
req2 = urllib.request.Request('http://127.0.0.1:8765/api/v1/player/bicycle/dismount', data=b'{}', headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req2) as resp:
    res2 = json.loads(resp.read().decode('utf-8'))
print("Dismount result:", res2.get("status"))

print("\n--- DIFF IN STATE (0x0223B680) ---")
for i in range(0x80):
    b1, b2 = snap_foot["state"][i], snap_bike["state"][i]
    if b1 != b2:
        print(f"  +0x{i:02X} (0x{0x0223B680 + i:08X}): {b1:02X} -> {b2:02X}")

print("\n--- DIFF IN ACTOR (0x0223DCE0) ---")
for i in range(0x80):
    b1, b2 = snap_foot["actor"][i], snap_bike["actor"][i]
    if b1 != b2:
        print(f"  +0x{i:02X} (0x{0x0223DCE0 + i:08X}): {b1:02X} -> {b2:02X}")

print("\n--- DIFF IN CORE (0x023329A0) ---")
for i in range(0x80):
    b1, b2 = snap_foot["core"][i], snap_bike["core"][i]
    if b1 != b2:
        print(f"  +0x{i:02X} (0x{0x023329A0 + i:08X}): {b1:02X} -> {b2:02X}")
