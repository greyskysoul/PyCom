"""ZMODEM protocol engine (pure Python), interoperable with lrzsz rz/sz.

Implements the classic ZMODEM handshake and transfer model described in the
ZMODEM spec (Chuck Forsberg) and used by lrzsz:

    Sender              Receiver
    -----               --------
    ZRQINIT
                        ZRINIT (capabilities + buffer size)
    ZFILE
                        ZRPOS (offset 0)
    ZDATA (binary) ...
    ZEOF
                        ZRINIT
    ZFIN
                        ZFIN
    OO

Scope / simplifications (kept compatible with the common lrzsz flow):
  * 16-bit CRC only (no CRC-32).
  * Hex headers for control frames; binary headers for ZFILE / ZDATA (the
    frame types that imply data packets follow).
  * Half-duplex: every data subpacket is terminated with ZCRCW and waits for
    a ZACK, matching the "no streaming" receiver model (9.4).
  * One file per session (our send dialog sends a single file).
  * No run-length encoding, no resume beyond offset 0, no ZSINIT.

Because it relies on exact frame encoding, validate against a real rz/sz
peer on the target device the first time you use it.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable
from typing import Optional

from pycom.i18n import tr
from pycom.xfer.ymodem import crc16

# --- manifest constants ----------------------------------------------------
ZPAD = 0x2A  # '*' pad; begins frames
ZDLE = 0x18  # Ctrl-X data-link escape
ZDLEE = 0x58  # ZDLE ^ 0x40 (escaped ZDLE)
ZBIN = 0x41  # 'A' binary frame indicator (CRC16)
ZHEX = 0x42  # 'B' hex frame indicator
ZBIN32 = 0x43  # 'C' binary frame with 32-bit CRC (unsupported here)

ZRQINIT = 0
ZRINIT = 1
ZSINIT = 2
ZACK = 3
ZFILE = 4
ZSKIP = 5
ZNAK = 6
ZABORT = 7
ZFIN = 8
ZRPOS = 9
ZDATA = 10
ZEOF = 11
ZFERR = 12
ZCRC = 13
ZCHALLENGE = 14
ZCOMPL = 15
ZCAN = 16

ZCRCE = 0x68  # CRC next, frame ends, header follows
ZCRCG = 0x69  # CRC next, frame continues nonstop
ZCRCQ = 0x6A  # CRC next, frame continues, ZACK expected
ZCRCW = 0x6B  # CRC next, ZACK expected, end of frame
ZRUB0 = 0x6C  # translate to rubout 0x7f
ZRUB1 = 0x6D  # translate to rubout 0xff

_HEX = "0123456789abcdef"
_FRAMEENDS = frozenset((ZCRCE, ZCRCG, ZCRCQ, ZCRCW))

# ZRINIT buffer length we advertise (bytes).  Non-zero + no CANOVIO tells the
# sender to use ZCRCW and wait for our ZACK after each subpacket.
_RX_BUFFER = 1024
_SUBPACKET = 1024

# Progress callback: cb(phase, filename, sent, total)
ProgressCB = Callable[[str, str, int, Optional[int]], None]


def _zdle_escape(data: bytes) -> bytes:
    """ZDLE-link-escape ``data`` (escape control / C1 characters)."""
    out = bytearray()
    for b in data:
        if (b & 0x60) == 0:
            out.append(ZDLE)
            out.append(b ^ 0x40)
        else:
            out.append(b)
    return bytes(out)


def hex_header(type_: int, p0: int, p1: int, p2: int, p3: int) -> bytes:
    """Build a hex header packet (ZPAD ZPAD ZDLE ZHEX ...)."""
    hdr = bytes([type_, p0, p1, p2, p3])
    crc = crc16(hdr)
    out = bytearray([ZPAD, ZPAD, ZDLE, ZHEX])
    for b in hdr + bytes([(crc >> 8) & 0xFF, crc & 0xFF]):
        out += _HEX[(b >> 4) & 0xF].encode("ascii")
        out += _HEX[b & 0xF].encode("ascii")
    return bytes(out)


def binary_header(type_: int, p0: int, p1: int, p2: int, p3: int) -> bytes:
    """Build a binary header packet (ZPAD ZDLE ZBIN ...)."""
    hdr = bytes([type_, p0, p1, p2, p3])
    crc = crc16(hdr)
    body = hdr + bytes([(crc >> 8) & 0xFF, crc & 0xFF])
    return bytes([ZPAD, ZDLE, ZBIN]) + _zdle_escape(body)


class ZModemEngine:
    """ZMODEM send/receive state machine over ``read``/``write``.

    Args are the same shape as :class:`pycom.xfer.ymodem.YModemEngine`:
      * ``read(timeout) -> bytes | None`` — must return at least one byte or
        ``None`` on timeout.
      * ``write(bytes)``
    """

    def __init__(
        self,
        read,
        write,
        *,
        timeout: float = 10.0,
        retries: int = 10,
        cancel: threading.Event | None = None,
        cb: ProgressCB | None = None,
    ) -> None:
        self._read = read
        self._write = write
        self.timeout = max(0.1, float(timeout))
        self.retries = max(1, int(retries))
        self.cancel = cancel or threading.Event()
        self.cb = cb or (lambda *_: None)
        self._buf = b""

    # ------------------------------------------------------------------ io
    def _raw_byte(self, timeout: float) -> int | None:
        """Read one raw byte from the wire (no ZDLE decoding)."""
        if not self._buf:
            chunk = self._read(timeout)
            if not chunk:
                return None
            self._buf += chunk
        b = self._buf[0]
        self._buf = self._buf[1:]
        return b

    def _read_exact(self, n: int, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        out = bytearray()
        while len(out) < n:
            if self.cancel.is_set():
                return None
            if not self._buf:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                chunk = self._read(min(remaining, 0.2))
                if not chunk:
                    continue
                self._buf += chunk
            take = min(n - len(out), len(self._buf))
            out += self._buf[:take]
            self._buf = self._buf[take:]
        return bytes(out)

    def _emit(self, phase: str, filename: str, sent: int, total: int | None) -> None:
        with contextlib.suppress(Exception):
            self.cb(tr(phase), filename, sent, total)

    def _cancel_bytes(self) -> bytes:
        return bytes([ZPAD, ZPAD, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18])

    # ------------------------------------------------------------ header io
    def _take_zdle(self, take) -> int | None:
        b = take()
        if b is None:
            return None
        if b == ZDLE:
            n = take()
            if n is None:
                return None
            return n ^ 0x40
        return b

    def _parse_hex_header_tail(self, take):
        nibbles = []
        for _ in range(14):
            b = take()
            if b is None:
                return None
            nibbles.append(_HEX.index(chr(b)))
        # two hex chars per byte: TYPE P0 P1 P2 P3 CRC-HI CRC-LO
        vals = [(nibbles[i] << 4) | nibbles[i + 1] for i in range(0, 14, 2)]
        hdr = bytes(vals[:5])
        crc = (vals[5] << 8) | vals[6]
        if crc16(hdr) != crc:
            return None
        return (hdr[0], hdr[1], hdr[2], hdr[3], hdr[4])

    def _parse_binary_header_tail(self, take):
        vals = []
        for _ in range(7):
            v = self._take_zdle(take)
            if v is None:
                return None
            vals.append(v)
        hdr = bytes(vals[:5])
        crc = (vals[5] << 8) | vals[6]
        if crc16(hdr) != crc:
            return None
        return (hdr[0], hdr[1], hdr[2], hdr[3], hdr[4])

    def _header_after_zpad(self):
        """Parse the header that follows an already-consumed ZPAD.

        Returns ``(header, leftover)`` where ``leftover`` is the raw bytes
        consumed that did not form a header (empty when a header was found).
        ``header`` is ``(type, p0, p1, p2, p3)`` or ``None``.
        """
        taken = bytearray()

        def take(timeout: float = 0.5) -> int | None:
            b = self._raw_byte(timeout)
            if b is not None:
                taken.append(b)
            return b

        b2 = take()
        if b2 is None:
            return None, bytes(taken)
        if b2 == ZDLE:
            fi = take()
            if fi is None:
                return None, bytes(taken)
            if fi == ZBIN:
                h = self._parse_binary_header_tail(take)
                return (h, b"") if h else (None, bytes(taken))
            return None, bytes(taken)
        if b2 == ZPAD:
            b3 = take()
            if b3 is None:
                return None, bytes(taken)
            if b3 == ZDLE:
                fi = take()
                if fi is None:
                    return None, bytes(taken)
                if fi == ZHEX:
                    h = self._parse_hex_header_tail(take)
                    return (h, b"") if h else (None, bytes(taken))
                if fi == ZBIN:
                    h = self._parse_binary_header_tail(take)
                    return (h, b"") if h else (None, bytes(taken))
                return None, bytes(taken)
            return None, bytes(taken)
        return None, bytes(taken)

    def _read_header(self, timeout: float | None = None) -> tuple | None:
        """Scan the stream for a valid ZMODEM header; returns (t,p0,p1,p2,p3)
        or ``None`` on timeout.  Leading garbage is discarded."""
        limit = timeout if timeout is not None else self.timeout
        end = time.monotonic() + limit
        while not self.cancel.is_set():
            if time.monotonic() > end:
                return None
            b = self._raw_byte(0.2)
            if b is None:
                continue
            if b != ZPAD:
                continue
            hdr, _leftover = self._header_after_zpad()
            if hdr is not None:
                return hdr
        return None

    def _boundary(self):
        """At a data/header boundary: decide whether the next frame is a
        header.  Returns ``(is_header, header)``.  When not a header any
        consumed bytes stay buffered so the caller reads them as data."""
        b = self._raw_byte(0.2)
        if b is None:
            return False, None
        if b != ZPAD:
            self._buf = bytes([b]) + self._buf
            return False, None
        hdr, leftover = self._header_after_zpad()
        if hdr is not None:
            return True, hdr
        self._buf = bytes([b]) + leftover + self._buf
        return False, None

    # ------------------------------------------------------------ data io
    def _read_data_subpacket(self, timeout: float):
        """Read one binary data subpacket.  Returns ``(data_bytes, frameend)``
        where ``frameend`` is the ZCRCE/ZCRCG/ZCRCQ/ZCRCW byte, ``None`` on
        timeout and ``("BAD", None)`` when the CRC does not match."""
        data = bytearray()
        while True:
            b = self._raw_byte(timeout)
            if b is None:
                return None, None
            if b == ZDLE:
                n = self._raw_byte(timeout)
                if n is None:
                    return None, None
                if (n & 0x60) == 0x40:
                    data.append(n ^ 0x40)
                    continue
                if n in _FRAMEENDS:
                    c1 = self._take_zdle(lambda: self._raw_byte(timeout))
                    c2 = self._take_zdle(lambda: self._raw_byte(timeout))
                    if c1 is None or c2 is None:
                        return None, None
                    got = (c1 << 8) | c2
                    expected = crc16(bytes(data) + bytes([n]))
                    if got != expected:
                        return "BAD", None
                    return bytes(data), n
                if n == ZRUB0:
                    data.append(0x7F)
                    continue
                if n == ZRUB1:
                    data.append(0xFF)
                    continue
                data.append(n ^ 0x40)
                continue
            data.append(b)

    def _write_data_subpacket(self, data: bytes, frameend: int = ZCRCW) -> None:
        crc = crc16(data + bytes([frameend]))
        out = (
            _zdle_escape(data)
            + bytes([ZDLE, frameend])
            + _zdle_escape(bytes([(crc >> 8) & 0xFF, crc & 0xFF]))
        )
        self._write(out)

    # ================================================================= SEND
    def send(
        self,
        stream,
        filename: str = "file.bin",
        size: int | None = None,
    ) -> tuple[bool, str]:
        """Send ``stream`` (opened 'rb') to a ZMODEM receiver."""
        name = os.path.basename(filename.replace("\\", "/")) or "file.bin"
        total = size
        if total is None:
            try:
                pos = stream.tell()
                stream.seek(0, os.SEEK_END)
                total = stream.tell()
                stream.seek(pos)
            except (OSError, ValueError):
                total = None
        total = total or 0  # unknown length: 0 in the ZEOF header / progress
        self._emit("等待对端握手…", name, 0, total)

        # 1) ZRQINIT -> ZRINIT
        ready = False
        for _ in range(self.retries):
            if self.cancel.is_set():
                return False, tr("用户取消")
            self._write(hex_header(ZRQINIT, 0, 0, 0, 0))
            hdr = self._read_header(self.timeout)
            if hdr is None:
                continue
            if hdr[0] == ZRINIT:
                ready = True
                break
            if hdr[0] == ZCHALLENGE:  # echo challenge and keep waiting
                self._write(hex_header(ZACK, hdr[1], hdr[2], hdr[3], hdr[4]))
        if not ready:
            return False, tr("无响应（对方未进入接收状态）")
        self._emit("发送文件信息…", name, 0, total)

        # 2) ZFILE (binary) + filename ZCRCW
        mtime = int(time.time())
        info = (f"{name}\0{total} {oct(mtime)[2:]} {oct(0o100644)[2:]}\0").encode(
            "utf-8", "replace"
        )
        self._write(binary_header(ZFILE, 0, 0, 0, 0))
        self._write_data_subpacket(info, ZCRCW)

        hdr = self._read_header(self.timeout)
        if hdr is None or hdr[0] not in (ZACK, ZRPOS):
            return False, tr("对端未确认文件信息")
        self._emit("传输中…", name, 0, total)

        # 3) ZDATA (binary) + ZCRCW subpackets, ack each
        self._write(binary_header(ZDATA, 0, 0, 0, 0))
        sent = 0
        while not self.cancel.is_set():
            chunk = stream.read(_SUBPACKET)
            if not chunk:
                break
            if not self._send_block_with_ack(chunk):
                return False, tr("对端未确认数据块")
            sent += len(chunk)
            self._emit("progress", name, sent, total)

        # 4) ZEOF
        self._write(
            hex_header(
                ZEOF, total & 0xFF, (total >> 8) & 0xFF, (total >> 16) & 0xFF, (total >> 24) & 0xFF
            )
        )

        # 5) finish handshake
        hdr = self._read_header(self.timeout)
        if hdr is not None and hdr[0] == ZRINIT:
            self._write(hex_header(ZFIN, 0, 0, 0, 0))
            hdr = self._read_header(self.timeout)
        if hdr is not None and hdr[0] == ZFIN:
            self._write(b"OO")
        self._emit("完成", name, sent, total)
        return True, tr("已发送 {n} 字节", n=sent)

    def _send_block_with_ack(self, chunk: bytes) -> bool:
        for _ in range(self.retries + 1):
            if self.cancel.is_set():
                return False
            self._write_data_subpacket(chunk, ZCRCW)
            hdr = self._read_header(self.timeout)
            if hdr is None:
                continue
            if hdr[0] in (ZACK, ZRPOS):
                return True
            if hdr[0] == ZNAK:
                continue
        return False

    # ================================================================= RECV
    def recv(self, open_file) -> tuple[bool, str, str | None]:
        """Receive one file from a ZMODEM sender.

        ``open_file(filename, size)`` must return a writable binary stream or
        ``None`` to refuse (abort).  Returns ``(ok, message, saved_name)``.
        """
        # 1) wait for ZRQINIT
        hdr = None
        end = time.monotonic() + self.timeout
        self._emit("等待发送方…", "", 0, None)
        while time.monotonic() < end and not self.cancel.is_set():
            h = self._read_header(1.0)
            if h is None:
                continue
            if h[0] == ZRQINIT:
                hdr = h
                break
        if hdr is None or hdr[0] != ZRQINIT:
            return False, tr("未收到发送方初始化请求"), None

        # 2) ZRINIT (advertise buffer size so sender uses ZCRCW)
        self._write(hex_header(ZRINIT, _RX_BUFFER & 0xFF, (_RX_BUFFER >> 8) & 0xFF, 0, 0))

        # 3) ZFILE
        hdr = self._read_header(self.timeout)
        if hdr is None or hdr[0] != ZFILE:
            return False, tr("未收到文件信息"), None

        # 4) filename subpacket
        info, fe = self._read_data_subpacket(self.timeout)
        if info is None or fe is None:
            return False, tr("未收到文件名"), None
        fname, fsize = _parse_file_info(info)
        if not fname:
            return False, tr("未收到文件名"), None
        stream = open_file(fname, fsize)
        if stream is None:
            self._write(self._cancel_bytes())
            return False, tr("拒绝接收 {name}", name=fname), fname

        self._write(hex_header(ZRPOS, 0, 0, 0, 0))
        self._emit("接收文件信息…", fname, 0, fsize)

        # 5) ZDATA header then data
        hdr = self._read_header(self.timeout)
        if hdr is None or hdr[0] != ZDATA:
            with contextlib.suppress(Exception):
                stream.close()
            return False, tr("未收到数据起始包"), fname

        sent = 0
        ok = False
        deadline = time.monotonic() + self.timeout
        while not self.cancel.is_set():
            if time.monotonic() > deadline:
                break
            is_header, hdr = self._boundary()
            if is_header:
                if hdr[0] == ZEOF:
                    ok = True
                    break
                deadline = time.monotonic() + self.timeout
                continue
            data, fe = self._read_data_subpacket(0.2)
            if data is None:
                continue
            if fe is None:  # bad CRC
                self._write(hex_header(ZNAK, 0, 0, 0, 0))
                break
            stream.write(data)
            sent += len(data)
            deadline = time.monotonic() + self.timeout
            self._emit("progress", fname, sent, fsize)
            if fe in (ZCRCW, ZCRCQ):
                self._write(hex_header(ZACK, 0, 0, 0, 0))

        with contextlib.suppress(Exception):
            stream.close()

        if not ok:
            self._write(self._cancel_bytes())
            return False, tr("接收失败"), fname

        # 6) announce ready-for-next, then ZFIN handshake
        self._write(hex_header(ZRINIT, _RX_BUFFER & 0xFF, (_RX_BUFFER >> 8) & 0xFF, 0, 0))
        hdr = self._read_header(self.timeout)
        if hdr is not None and hdr[0] == ZFIN:
            self._write(hex_header(ZFIN, 0, 0, 0, 0))
        self._emit("完成", fname, sent, fsize)
        return True, tr("已接收 {name} ({n} 字节)", name=fname, n=sent), fname


def _parse_file_info(data: bytes) -> tuple[str, int | None]:
    """Parse a ZFILE subpacket -> (filename, size|None)."""
    text = data.split(b"\x00", 1)[0]
    fields = text.split()
    name = fields[0].decode("utf-8", "replace") if fields else ""
    size: int | None = None
    if len(fields) >= 2:
        with contextlib.suppress(ValueError):
            size = int(fields[1])
    return name, size
