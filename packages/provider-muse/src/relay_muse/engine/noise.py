"""The muse tunnel's wire: a Noise_XX_25519_AESGCM_SHA256 initiator and the protobuf frames of HTTP-over-Noise.

Pure (no I/O). Shapes from the spike of 26/10 and the web client's bundle (fixtures/muse/README.md):
- every WebSocket binary frame is one AES-GCM ciphertext of a `NoiseTransportFrame` {1 chunk_id, 2 chunk_index,
  3 total_chunks, 4 payload}; a payload over `CHUNK_BYTES` is cut into chunks of one chunk_id;
- the payload, client to server, is a `ServiceRequest` {1 service (0 = DAEMON, omitted), 2 ServiceFrame}; server to
  client a `ServiceResponse` {1 ServiceFrame};
- a `ServiceFrame` is {1 stream_id, then one of 2 request, 3 response, 4 body_chunk, 5 reset}: a request {1 verb,
  2 path, 3 header {1 key, 2 value}, 4 body, 5 end_body}, a response {1 status, 2 header, 3 body, 4 end_body}, a body
  chunk {1 data, 2 end_body}, a reset {1 code, 2 reason}.
The handshake's first message carries `0a 20` + 32 random bytes (the client's freshness nonce), the third none.
"""

import hashlib
import hmac
import struct
from typing import NamedTuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROTOCOL = b"Noise_XX_25519_AESGCM_SHA256"
CHUNK_BYTES = 48 * 1024      # the web client's chunk size; the server refuses a frame payload over 65535 bytes
DAEMON, SENTINEL = 0, 1      # services: the application plane, and the one that decides approvals


# ---- protobuf, the few wire types the frames use -----------------------------------------------------------------
def _varint(n: int) -> bytes:
    out = bytearray()
    n &= (1 << 64) - 1
    while True:
        b, n = n & 0x7F, n >> 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def field(num: int, value: int | bool | str | bytes) -> bytes:
    """One field: an int or bool as a varint, a str or bytes length-delimited."""
    if isinstance(value, bool | int):
        return _varint(num << 3) + _varint(int(value))
    data = value.encode() if isinstance(value, str) else value
    return _varint(num << 3 | 2) + _varint(len(data)) + data


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise ValueError("a truncated varint")
        c = buf[i]
        i += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return n, i


def parse(buf: bytes) -> dict[int, list]:
    """field number -> its values in order: a varint as an int, a length-delimited field as bytes. ValueError on a
    malformed message."""
    out: dict[int, list] = {}
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _read_varint(buf, i)
        elif wire == 2:
            size, i = _read_varint(buf, i)
            if i + size > len(buf):
                raise ValueError("a truncated field")
            value, i = buf[i:i + size], i + size
        elif wire in (1, 5):
            width = 8 if wire == 1 else 4
            value, i = buf[i:i + width], i + width
        else:
            raise ValueError("wire type %d" % wire)
        out.setdefault(num, []).append(value)
    return out


def _first(fields: dict[int, list], num: int, default=None):
    return fields.get(num, [default])[0]


# ---- Noise_XX initiator --------------------------------------------------------------------------------------------
def hkdf2(chaining_key: bytes, ikm: bytes) -> tuple[bytes, bytes]:
    temp = hmac.new(chaining_key, ikm, hashlib.sha256).digest()
    one = hmac.new(temp, b"\x01", hashlib.sha256).digest()
    return one, hmac.new(temp, one + b"\x02", hashlib.sha256).digest()


def public_bytes(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def dh(key: X25519PrivateKey, public: bytes) -> bytes:
    return key.exchange(X25519PublicKey.from_public_bytes(public))


class Cipher:
    """AES-GCM with the Noise nonce: 4 zero bytes and a big-endian 64-bit counter."""

    def __init__(self, key: bytes):
        self._aead, self.n = AESGCM(key), 0

    def _nonce(self) -> bytes:
        nonce = b"\0\0\0\0" + struct.pack(">Q", self.n)
        self.n += 1
        return nonce

    def encrypt(self, ad: bytes, plaintext: bytes) -> bytes:
        return self._aead.encrypt(self._nonce(), plaintext, ad)

    def decrypt(self, ad: bytes, ciphertext: bytes) -> bytes:
        return self._aead.decrypt(self._nonce(), ciphertext, ad)


class Symmetric:
    """Noise's SymmetricState: the handshake hash, the chaining key and the current cipher (None before a key)."""

    def __init__(self) -> None:
        self.h = PROTOCOL.ljust(32, b"\0")
        self.ck = self.h
        self.cipher: Cipher | None = None
        self.mix_hash(b"")       # an empty prologue

    def mix_hash(self, data: bytes) -> None:
        self.h = hashlib.sha256(self.h + data).digest()

    def mix_key(self, ikm: bytes) -> None:
        self.ck, key = hkdf2(self.ck, ikm)
        self.cipher = Cipher(key)

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        out = self.cipher.encrypt(self.h, plaintext) if self.cipher else plaintext
        self.mix_hash(out)
        return out

    def decrypt_and_hash(self, data: bytes) -> bytes:
        out = self.cipher.decrypt(self.h, data) if self.cipher else data
        self.mix_hash(data)
        return out

    def split(self) -> tuple[Cipher, Cipher]:
        first, second = hkdf2(self.ck, b"")
        return Cipher(first), Cipher(second)


class Initiator:
    """-> e; <- e, ee, s, es; -> s, se. `split()` after message three gives (send, receive)."""

    def __init__(self, static: X25519PrivateKey | None = None, ephemeral: X25519PrivateKey | None = None):
        self.state = Symmetric()
        self.s = static or X25519PrivateKey.generate()
        self.e = ephemeral or X25519PrivateKey.generate()
        self.re = b""

    def message1(self, payload: bytes) -> bytes:
        e = public_bytes(self.e)
        self.state.mix_hash(e)
        return e + self.state.encrypt_and_hash(payload)

    def read2(self, message: bytes) -> bytes:
        """The responder's message; raises (InvalidTag, ValueError) when it does not authenticate."""
        if len(message) < 32 + 48 + 16:
            raise ValueError("handshake message 2 is too short")
        self.re = message[:32]
        self.state.mix_hash(self.re)
        self.state.mix_key(dh(self.e, self.re))
        rs = self.state.decrypt_and_hash(message[32:80])
        self.state.mix_key(dh(self.e, rs))
        return self.state.decrypt_and_hash(message[80:])

    def message3(self, payload: bytes) -> tuple[bytes, Cipher, Cipher]:
        out = self.state.encrypt_and_hash(public_bytes(self.s))
        self.state.mix_key(dh(self.s, self.re))
        out += self.state.encrypt_and_hash(payload)
        send, receive = self.state.split()
        return out, send, receive


# ---- HTTP-over-Noise frames ----------------------------------------------------------------------------------------
def request_payload(stream_id: int, verb: str, path: str, headers: list[tuple[str, str]], body: bytes,
                    service: int = DAEMON) -> bytes:
    """A whole `ServiceRequest` for one request (body included, ended)."""
    request = field(1, verb) + field(2, path) + b"".join(field(3, field(1, k) + field(2, v)) for k, v in headers) \
        + (field(4, body) if body else b"") + field(5, True)
    frame = field(1, stream_id) + field(2, request)
    return (field(1, service) if service else b"") + field(2, frame)


def chunks(chunk_id: int, payload: bytes, size: int = CHUNK_BYTES) -> list[bytes]:
    """The `NoiseTransportFrame`s (plaintext) that carry one payload."""
    total = max(1, -(-len(payload) // size))
    return [field(1, chunk_id) + field(2, i) + field(3, total) + field(4, payload[i * size:(i + 1) * size])
            for i in range(total)]


class Part(NamedTuple):
    """One piece of a server stream: a `response` (status, headers, body), a `body` chunk or a `reset`."""
    stream_id: int
    kind: str                     # response | body | reset
    status: int = 0
    headers: tuple[tuple[str, str], ...] = ()
    data: bytes = b""
    end: bool = False
    reason: str = ""


class Reassembler:
    """Transport frames in, whole `Part`s out (a chunked message is kept until its last chunk)."""

    def __init__(self) -> None:
        self._open: dict[int, dict[int, bytes]] = {}

    def feed(self, plaintext: bytes) -> Part | None:
        f = parse(plaintext)
        chunk_id, index, total = _first(f, 1, 0), _first(f, 2, 0), _first(f, 3, 1)
        pieces = self._open.setdefault(chunk_id, {})
        pieces[index] = _first(f, 4, b"")
        if len(pieces) < total:
            return None
        del self._open[chunk_id]
        payload = b"".join(pieces.get(i, b"") for i in range(total))
        return decode_response(payload)


def decode_response(payload: bytes) -> Part | None:
    """A `ServiceResponse` as a `Part`; None for a frame of a kind the client does not use."""
    frame = parse(_first(parse(payload), 1, b""))
    sid = _first(frame, 1, 0)
    if 5 in frame:
        reset = parse(frame[5][0])
        return Part(sid, "reset", status=_first(reset, 1, 0), end=True,
                    reason=_first(reset, 2, b"").decode("utf-8", "replace"))
    if 3 in frame:
        r = parse(frame[3][0])
        headers = tuple((_first(h, 1, b"").decode("utf-8", "replace"), _first(h, 2, b"").decode("utf-8", "replace"))
                        for h in map(parse, r.get(2, [])))
        return Part(sid, "response", status=_first(r, 1, 0), headers=headers, data=_first(r, 3, b""),
                    end=bool(_first(r, 4, 0)))
    if 4 in frame:
        chunk = parse(frame[4][0])
        return Part(sid, "body", data=_first(chunk, 1, b""), end=bool(_first(chunk, 2, 0)))
    return None
