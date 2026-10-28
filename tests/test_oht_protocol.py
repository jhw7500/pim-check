"""Source-derived synthetic fixtures; these are not board packet captures."""

import pytest

from oht_protocol import build_version_request, parse_version_response


@pytest.mark.parametrize(
    ("machine_type", "expected_hex"),
    [
        (12851, "3233414243313233a02c03"),
        (12853, "3235414243313233a02c03"),
        (12855, "3237414243313233a02c03"),
    ],
)
def test_version_request_matches_literal_network_order_packet(machine_type, expected_hex):
    assert build_version_request(b"ABC123", machine_type) == bytes.fromhex(expected_hex)


def test_default_request_preserves_exact_binary_machine_id():
    assert build_version_request(b"\x00A\xff\x00B\x80") == bytes.fromhex(
        "32330041ff004280a02c03"
    )


@pytest.mark.parametrize("machine_id", [b"", b"ABCDE", b"ABCDEFG"])
def test_request_rejects_machine_id_without_exact_six_bytes(machine_id):
    with pytest.raises(ValueError):
        build_version_request(machine_id)


@pytest.mark.parametrize("machine_id", ["ABC123", bytearray(b"ABC123"), None])
def test_request_requires_bytes_machine_id(machine_id):
    with pytest.raises(TypeError):
        build_version_request(machine_id)


@pytest.mark.parametrize("machine_type", [0, 6, 12852, -1, 65536])
def test_request_rejects_machine_type_not_supported_by_ord(machine_type):
    with pytest.raises(ValueError):
        build_version_request(b"ABC123", machine_type)


@pytest.mark.parametrize("machine_type", [True, False, 12851.0, "12851", None])
def test_request_requires_integer_machine_type_and_rejects_bool(machine_type):
    with pytest.raises(TypeError):
        build_version_request(b"ABC123", machine_type)


def test_parse_literal_version_response_returns_raw_five_bytes():
    reply = bytes.fromhex("0006414243313233a02d03312e322e33")
    assert parse_version_response(reply, b"ABC123") == b"1.2.3"


def test_parse_preserves_nul_and_non_ascii_version_bytes():
    reply = bytes.fromhex("00060041ff004280a02d0300ff410080")
    assert parse_version_response(reply, b"\x00A\xff\x00B\x80") == b"\x00\xffA\x00\x80"


@pytest.mark.parametrize(
    "reply_hex",
    [
        "3233414243313233a02d03312e322e33",  # request machine type, not blackbox
        "06004142433132332da003312e322e33",  # little-endian header
        "00064142433132332da003312e322e33",  # only command is little-endian
        "0006414243313233a02c03312e322e33",  # request command, not response
        "0006414243313233a02e03312e322e33",  # error command
        "0006414243313233a02d01312e322e33",  # COPY
        "0006414243313233a02d02312e322e33",  # PRI
        "0006414243313234a02d03312e322e33",  # another machine ID
        "7b22524550223a22312e322e33227d2020",  # JSON, exactly 16 bytes
    ],
)
def test_parse_rejects_unrelated_or_malformed_response(reply_hex):
    with pytest.raises(ValueError):
        parse_version_response(bytes.fromhex(reply_hex), b"ABC123")


@pytest.mark.parametrize("length", [0, 1, 9, 10, 11, 12, 15, 17, 32])
def test_parse_requires_exactly_one_complete_sixteen_byte_response(length):
    reply = bytes.fromhex("0006414243313233a02d03312e322e33")
    data = reply[:length] if length < 16 else reply + b"\x00" * (length - 16)
    with pytest.raises(ValueError):
        parse_version_response(data, b"ABC123")


@pytest.mark.parametrize("data", ["not bytes", bytearray(16), None])
def test_parse_requires_bytes_response(data):
    with pytest.raises(TypeError):
        parse_version_response(data, b"ABC123")


@pytest.mark.parametrize("machine_id", [b"", b"ABCDE", b"ABCDEFG"])
def test_parse_requires_exact_expected_id_length(machine_id):
    reply = bytes.fromhex("0006414243313233a02d03312e322e33")
    with pytest.raises(ValueError):
        parse_version_response(reply, machine_id)


@pytest.mark.parametrize("machine_id", ["ABC123", bytearray(b"ABC123"), None])
def test_parse_requires_bytes_expected_machine_id(machine_id):
    reply = bytes.fromhex("0006414243313233a02d03312e322e33")
    with pytest.raises(TypeError):
        parse_version_response(reply, machine_id)
