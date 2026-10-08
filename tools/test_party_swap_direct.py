import sys, os
sys.path.insert(0, os.path.abspath("."))
import urllib.request, json, asyncio
from backend.black2.bizhawk.bridge_client import BridgeClient
from backend.black2.bizhawk.transport import BizHawkTransport

async def main():
    transport = BizHawkTransport(port=8766)
    await transport.connect()
    client = BridgeClient(transport)
    
    # 1. Resolve party_ptr
    gd_res = await client.read_bytes(0x0223B704, 4)
    party_ptr = int.from_bytes(bytes(gd_res), "little")
    print("Party pointer: 0x%08X" % party_ptr)
    
    # 2. Read capacity & count
    hdr = await client.read_bytes(party_ptr, 8)
    capacity = int.from_bytes(bytes(hdr[0:4]), "little")
    count = int.from_bytes(bytes(hdr[4:8]), "little")
    print("Capacity: %d, Count: %d" % (capacity, count))
    
    slot_a = 1
    slot_b = 3
    addr_a = party_ptr + 8 + (slot_a - 1) * 0xDC
    addr_b = party_ptr + 8 + (slot_b - 1) * 0xDC
    
    data_a = await client.read_bytes(addr_a, 0xDC)
    data_b = await client.read_bytes(addr_b, 0xDC)
    
    pid_a = int.from_bytes(bytes(data_a[0:4]), "little")
    pid_b = int.from_bytes(bytes(data_b[0:4]), "little")
    print("Slot %d PID before: 0x%08X" % (slot_a, pid_a))
    print("Slot %d PID before: 0x%08X" % (slot_b, pid_b))
    
    # Swap in Main RAM
    await client.write_bytes(addr_a, data_b)
    await client.write_bytes(addr_b, data_a)
    print("Main RAM write complete!")
    
    # Verify
    new_data_a = await client.read_bytes(addr_a, 0xDC)
    new_data_b = await client.read_bytes(addr_b, 0xDC)
    new_pid_a = int.from_bytes(bytes(new_data_a[0:4]), "little")
    new_pid_b = int.from_bytes(bytes(new_data_b[0:4]), "little")
    print("Slot %d PID after: 0x%08X (expected 0x%08X)" % (slot_a, new_pid_a, pid_b))
    print("Slot %d PID after: 0x%08X (expected 0x%08X)" % (slot_b, new_pid_b, pid_a))
    
    await transport.close()

asyncio.run(main())