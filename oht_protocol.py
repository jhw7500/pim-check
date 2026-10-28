"""Offline codec for ORD EVT_TEST version packets.

Contract source: pim-package-jhw commit
f26b99fabda5955229041ecd182091fb8b340137, ord/tcpServer.h and
ord/tcpServer.cpp (parseTcpRecvData / EVT_TEST):
https://github.com/jhw7500/pim-package-jhw/blob/
f26b99fabda5955229041ecd182091fb8b340137/ord/tcpServer.cpp

Fixtures are source-derived, not board captures. This module does not send
requests or frame a TCP stream. The five returned bytes are not a full package
version, a success flag, or a source/build identity.
"""

import struct


_REQUEST = struct.Struct("!H6sHB")
_RESPONSE = struct.Struct("!H6sHB5s")
_REQUEST_MACHINE_TYPES = (12851, 12853, 12855)


def _validate_machine_id(machine_id: bytes) -> None:
    if not isinstance(machine_id, bytes):
        raise TypeError("machine_id must be bytes")
    if len(machine_id) != 6:
        raise ValueError("machine_id must contain exactly 6 bytes")


def build_version_request(machine_id: bytes, machine_type: int = 12851) -> bytes:
    """Build one 11-byte version request with an explicit six-byte machine ID."""
    _validate_machine_id(machine_id)
    if isinstance(machine_type, bool) or not isinstance(machine_type, int):
        raise TypeError("machine_type must be an integer, not bool")
    if machine_type not in _REQUEST_MACHINE_TYPES:
        raise ValueError("machine_type must be 12851, 12853, or 12855")
    return _REQUEST.pack(machine_type, machine_id, 41004, 3)


def parse_version_response(data: bytes, expected_machine_id: bytes) -> bytes:
    """Validate one complete 16-byte reply and preserve its five version bytes.

    The caller supplies the expected ID; unrelated, partial, or concatenated
    frames are rejected. No text decoding or NUL stripping is performed.
    """
    _validate_machine_id(expected_machine_id)
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) != _RESPONSE.size:
        raise ValueError("version response must contain exactly 16 bytes")
    machine_type, machine_id, command, event_type, version = _RESPONSE.unpack(data)
    if machine_type != 6:
        raise ValueError("version response machine_type must be 6")
    if machine_id != expected_machine_id:
        raise ValueError("version response machine_id does not match the expected ID")
    if command != 41005:
        raise ValueError("version response command must be 41005")
    if event_type != 3:
        raise ValueError("version response event_type must be 3")
    return version
