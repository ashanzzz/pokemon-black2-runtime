"""Abstract transport definitions for BizHawk Bridge."""

import abc
from typing import Dict, Any, Optional


class BizHawkTransport(abc.ABC):
    """Unified abstract transport interface for BizHawk Bridge (HTTP Attach or TCP Socket)."""

    @abc.abstractmethod
    async def connect(self) -> bool:
        """Connect or start transport."""
        pass

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Disconnect transport."""
        pass

    @abc.abstractmethod
    async def request(self, op: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 3.0) -> Dict[str, Any]:
        """Send RPC request and await response."""
        pass

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Return True if bridge is connected and active."""
        pass

    @abc.abstractmethod
    def get_transport_type(self) -> str:
        """Return transport name ('http' or 'socket')."""
        pass
