"""Payload-level tests for bounded BizHawk reverse-engineering probes."""

import asyncio
import unittest

from backend.black2.bizhawk.bridge_client import BridgeClient


class FakeTransport:
    def __init__(self):
        self.requests = []

    async def request(self, operation, payload=None):
        self.requests.append((operation, payload))
        return {"operation": operation, "payload": payload}

    def is_connected(self):
        return True


class TestBridgeClientTraceMethods(unittest.TestCase):
    def test_address_specific_trace_payload_keeps_atomic_button_edge(self):
        transport = FakeTransport()
        client = BridgeClient(transport)

        result = asyncio.run(client.begin_memory_write_trace(
            0x023353C0,
            0xF00,
            [0x023353EC, 0x023353F0],
            3,
            32,
            "A",
            [{"id": "control", "domain": "Main RAM", "offset": 0x332B40, "length": 0x200}],
        ))

        self.assertEqual(result["operation"], "probe.write_trace_begin")
        operation, payload = transport.requests[-1]
        self.assertEqual(operation, "probe.write_trace_begin")
        self.assertEqual(payload["addresses"], [0x023353EC, 0x023353F0])
        self.assertEqual(payload["button"], "A")
        self.assertEqual(payload["max_frames"], 3)
        self.assertEqual(payload["max_events"], 32)

    def test_trace_capability_and_cancel_operations_are_read_only_control_paths(self):
        transport = FakeTransport()
        client = BridgeClient(transport)

        asyncio.run(client.get_memory_write_trace_capabilities())
        asyncio.run(client.get_memory_write_trace())
        asyncio.run(client.cancel_memory_write_trace())

        self.assertEqual(
            [operation for operation, _ in transport.requests],
            ["bridge.trace_capabilities", "probe.write_trace_status", "probe.write_trace_cancel"],
        )

