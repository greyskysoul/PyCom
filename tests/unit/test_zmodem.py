"""ZMODEM engine round-trip tests (sender <-> receiver over an in-memory pipe)."""

from __future__ import annotations

import threading
import time

from pycom.xfer.zmodem import ZModemEngine


class _Pipe:
    """A thread-safe byte pipe implementing the engine read/write contract:
    ``read(timeout) -> bytes | None`` (at least one byte or None on timeout)."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._cond = threading.Condition()

    def write(self, data: bytes) -> None:
        with self._cond:
            self._buf += data
            self._cond.notify_all()

    def read(self, timeout: float) -> bytes | None:
        with self._cond:
            end = time.monotonic() + timeout
            while not self._buf:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            data = bytes(self._buf)
            self._buf.clear()
            return data


def _roundtrip(tmp_path, payload: bytes, filename: str = "blob.bin") -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(payload)

    to_sender = _Pipe()  # receiver -> sender
    to_receiver = _Pipe()  # sender -> receiver
    sender = ZModemEngine(read=to_sender.read, write=to_receiver.write, timeout=5.0, retries=5)
    receiver = ZModemEngine(read=to_receiver.read, write=to_sender.write, timeout=5.0, retries=5)

    results: dict = {}
    out = tmp_path / "out.bin"

    def do_send() -> None:
        with open(src, "rb") as fh:
            results["send"] = sender.send(fh, filename=filename)

    def do_recv() -> None:
        def open_file(name, size):
            results["name"] = name
            return open(out, "wb")

        results["recv"] = receiver.recv(open_file)

    t1 = threading.Thread(target=do_send, name="z-send", daemon=True)
    t2 = threading.Thread(target=do_recv, name="z-recv", daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)
    assert not t1.is_alive(), "sender did not finish"
    assert not t2.is_alive(), "receiver did not finish"

    ok_send, msg_send = results["send"]
    ok_recv, msg_recv, name = results["recv"]
    assert ok_send, f"send failed: {msg_send}"
    assert ok_recv, f"recv failed: {msg_recv}"
    assert name == filename
    assert results["name"] == filename
    assert out.read_bytes() == payload


def test_zmodem_roundtrip_binary_data(tmp_path):
    # every byte value, repeated so the file is larger than one subpacket and
    # includes ZDLE/ZPAD/control/high-bit bytes
    payload = (bytes(range(256)) * 20)[:5300]
    _roundtrip(tmp_path, payload)


def test_zmodem_roundtrip_small_and_all_control(tmp_path):
    _roundtrip(tmp_path, bytes([0x00, 0x11, 0x13, 0x18, 0x2A, 0x41, 0x7F, 0xFF]))


def test_zmodem_roundtrip_exact_subpacket_boundary(tmp_path):
    payload = bytes((i * 7) & 0xFF for i in range(1024))  # exactly one subpacket
    _roundtrip(tmp_path, payload)


def test_zmodem_roundtrip_empty_file(tmp_path):
    _roundtrip(tmp_path, b"", filename="empty.bin")
