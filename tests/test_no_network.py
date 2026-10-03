import socket

import pytest
from pytest_socket import SocketConnectBlockedError


def test_outbound_connection_is_blocked():
    # 203.0.113.0/24 is TEST-NET-3; the block must trigger before any packet is sent.
    with pytest.raises(SocketConnectBlockedError):
        socket.create_connection(("203.0.113.10", 443), timeout=1)
