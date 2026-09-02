import pytest

BANNER_TIMEOUT = 60.0


def _wait_ready(lab):
    lab.dut_uart.expect(r"^boot: ok$", BANNER_TIMEOUT)
    lab.gw_uart.expect(r"^GW ready can1=ok$", BANNER_TIMEOUT)


def test_can_echo_increments_id(lab):
    _wait_ready(lab)
    lab.can_send(0x123, bytes([0xDE, 0xAD, 0xBE, 0xEF]))
    can_id, extended, payload = lab.can_recv()
    assert not extended
    assert can_id == 0x124
    assert payload == bytes([0xDE, 0xAD, 0xBE, 0xEF])


RENODE_DLC0_BUG = (
    "Renode 1.16.1: STMCAN hands the CAN hub a frame with Data == null for DLC 0, "
    "and CANHub.Transmit only catches RecoverableException, so the emulator process "
    "dies with a NullReferenceException in CANMessageFrame.ToSocketCAN"
)


@pytest.mark.parametrize(
    "can_id, data",
    [
        (0x000, b"\x00"),
        (0x7FE, bytes(range(8))),
        (0x7FF, b"\x01"),  # wraps to 0x000 in the 11-bit space
        pytest.param(0x010, b"", marks=pytest.mark.skip(reason=RENODE_DLC0_BUG)),
    ],
)
def test_can_echo_edge_cases(lab, can_id, data):
    _wait_ready(lab)
    lab.can_send(can_id, data)
    rx_id, extended, payload = lab.can_recv()
    assert not extended
    assert rx_id == (can_id + 1) & 0x7FF
    assert payload == data


def test_can_echo_extended_id(lab):
    _wait_ready(lab)
    lab.can_send(0x1ABCDEF0, b"\x42\x43", extended=True)
    rx_id, extended, payload = lab.can_recv()
    assert extended
    assert rx_id == 0x1ABCDEF1
    assert payload == b"\x42\x43"


def test_can_burst(lab):
    _wait_ready(lab)
    for i in range(5):
        lab.can_send(0x100 + i, bytes([i]))
        rx_id, _, payload = lab.can_recv()
        assert rx_id == 0x101 + i
        assert payload == bytes([i])
