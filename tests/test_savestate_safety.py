import asyncio
from types import SimpleNamespace

import pytest

from fastapi import HTTPException

from backend.black2.bizhawk.bridge_client import BridgeClient
from backend.black2.bizhawk.savestate import (
    BRIDGE_UNAVAILABLE_CODE,
    INCOMPATIBLE_CODE,
    UNCONFIRMED_CODE,
    classify_bridge_unavailable,
    classify_load_failure,
    is_confirmed_load,
)


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def is_connected(self):
        return True

    async def request(self, operation, payload=None):
        self.requests.append((operation, payload))
        return self.response


def test_incompatible_bizhawk_message_is_classified_as_conflict_and_preserves_game():
    detail = classify_load_failure(
        {"status": "error", "error": "This savestate was made with a different core or different sync settings. Loadstate cancelled"},
        slot=4,
    )

    assert detail["code"] == INCOMPATIBLE_CODE
    assert detail["http_status"] == 409
    assert detail["preserved_current_game"] is True
    assert detail["retryable"] is False


def test_false_load_result_is_never_confirmed():
    result = {"slot": 4, "status": "incompatible", "loaded": False, "confirmed": False}
    assert is_confirmed_load(result) is False
    detail = classify_load_failure(result, slot=4)
    assert detail["code"] == INCOMPATIBLE_CODE


def test_old_bridge_payload_is_unconfirmed_instead_of_false_success():
    result = {"slot": 4, "loaded": True}
    assert is_confirmed_load(result) is False
    detail = classify_load_failure({"status": "unconfirmed"}, slot=4)
    assert detail["code"] == UNCONFIRMED_CODE
    assert detail["http_status"] == 503


def test_bridge_client_keeps_savestate_payload_for_api_classification():
    transport = FakeTransport({"status": "incompatible", "loaded": False, "confirmed": False, "slot": 2})
    client = BridgeClient(transport)

    result = asyncio.run(client.load_state(2))

    assert result["status"] == "incompatible"
    assert transport.requests == [("savestate.load", {"slot": 2})]


def test_load_route_returns_conflict_for_a_rejected_bridge_result(monkeypatch):
    from backend.black2.api import app as app_module

    class FakeClient:
        is_connected = True

        async def load_state(self, slot):
            return {
                "slot": slot,
                "status": "incompatible",
                "loaded": False,
                "confirmed": False,
                "error": "different core or different sync settings; loadstate cancelled",
            }

    monkeypatch.setattr(app_module, "client", FakeClient())

    with pytest.raises(HTTPException) as raised:
        asyncio.run(app_module.post_savestate_load(3))

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == INCOMPATIBLE_CODE
    assert raised.value.detail["preserved_current_game"] is True


def test_load_route_rejects_legacy_unconfirmed_success(monkeypatch):
    from backend.black2.api import app as app_module

    class FakeClient:
        is_connected = True

        async def load_state(self, slot):
            return {"slot": slot, "loaded": True}

    monkeypatch.setattr(app_module, "client", FakeClient())
    monkeypatch.setattr(
        app_module,
        "probe_bizhawk_process",
        lambda: SimpleNamespace(pid=None, popup={}),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(app_module.post_savestate_load(3))

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == UNCONFIRMED_CODE


def test_load_route_classifies_and_dismisses_native_mismatch_after_rpc_stall(monkeypatch):
    from backend.black2.api import app as app_module

    class FakeClient:
        is_connected = True

        async def load_state(self, slot):
            raise TimeoutError("Bridge socket command 'savestate.load' timed out")

    popup = {
        "present": True,
        "blocking": True,
        "kind": "savestate_sync_settings_mismatch",
        "title": "Savestate sync settings mismatch",
        "text": "This savestate was made with a different core or different sync settings.",
        "api_dismissible": True,
        "_window_handle": 1,
        "_button_handle": 2,
    }
    monkeypatch.setattr(app_module, "client", FakeClient())
    monkeypatch.setattr(
        app_module,
        "probe_bizhawk_process",
        lambda: SimpleNamespace(pid=1234, popup=popup),
    )
    dismiss_calls = []
    monkeypatch.setattr(
        app_module,
        "dismiss_bizhawk_popup",
        lambda pid: dismiss_calls.append(pid) or {"ok": True, "dismissed": True},
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(app_module.post_savestate_load(6))

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == INCOMPATIBLE_CODE
    assert raised.value.detail["preserved_current_game"] is True
    assert raised.value.detail["popup"]["kind"] == "savestate_sync_settings_mismatch"
    assert raised.value.detail["popup_dismissal"]["dismissed"] is True
    assert dismiss_calls == [1234]


def test_disconnected_bridge_returns_a_recoverable_structured_error():
    detail = classify_bridge_unavailable(slot=1)
    assert detail["code"] == BRIDGE_UNAVAILABLE_CODE
    assert detail["http_status"] == 503
    assert detail["preserved_current_game"] is True
