"""dola's `a_bogus` query signature, in pure Python (stdlib only, no browser, no Node).

dola's page gets `a_bogus` from ByteDance's `bdms` SDK (`bdms-sdk.a68c472ba3.js`, version block 1.0.1.0), which wraps
`fetch`. dola checks it on `POST /chat/completion` only (reads pass without one); a bad one comes back as a
710022002/710022004 "throttle" with no conversation. The format was decoded from 92 signatures dola's own page made
and checked against them (`fixtures/dola/api-spike-261023/page_signatures.json`, FINDINGS "HTTP send spike 23/10"):

    a_bogus = b64_s4( mask(3, 82) + rc4'(0xD3, version block 1,0,1,0 + noise-expand(body)) )
    body    = 50 one-byte fields in a fixed order + screen string + "<(now+3) & 255>," + XOR checksum

The fields bind three inputs by 3 bytes each: SM3(SM3(query + "dhzx")) (the query exactly as sent, without a_bogus),
SM3(SM3(body + "dhzx")) (the exact body bytes) and SM3(b64_s3(rc4'([env >> 8, env & 255, detect], UA))); the rest are
dola's constants (aid, page id), the clock and the page's state flags (detect 14 on a page's first call, 8 after).
"""

import random
import re
import struct
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlencode

SCREEN_LINUX_1440 = "1440|900|1448|1031|1440|900|1440|900|Linux x86_64"   # the box Chrome's, decoded from dola's page
AID = 495671
PAGE_ID = 26930
MAGIC = (3, 82)                 # header of every a_bogus
SDK_VERSION = (1, 0, 1, 0)      # version block of bdms 1.0.1.0
BDMS_SDK_FILE = "bdms-sdk.a68c472ba3.js"
BDMS_VERSION = "1.0.1.0"

ENV = 1                         # env flags: 1 on every page signature
DETECT_FIRST, DETECT_LATER = 14, 8   # detect flags: a page's first call(s), then the rest
NR_TAG_FIRST, NR_TAG_LATER = (0, 0, 0, 0), (1, 1, 1, 0)   # travels with detect
NR = 1
CALL_BUCKET = 6
SINCE_INIT_MS = 3               # 3 on 85 of 92 page signatures, 4 on the rest
TRIPWIRE = 3
FIELD_24 = 41
SALT = b"dhzx"                  # appended before the double SM3 of the query and the body

S4 = "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe"   # alphabet of a_bogus itself
S3 = "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe"   # alphabet inside the UA digest
# field number -> position in the body: field ORDER[i] is body byte i
ORDER = (34, 44, 56, 61, 73, 29, 70, 45, 35, 49, 38, 66, 51, 68, 28, 48, 64, 47, 30, 71, 26, 55, 31, 69, 59, 40,
         62, 63, 27, 72, 41, 74, 57, 52, 42, 39, 33, 67, 53, 43, 65, 46, 36, 24, 60, 32, 79, 80, 84, 85)
EPOCH_MS = 1_721_836_800_000    # origin of the fortnight counter (field 26)
FORTNIGHT_MS = 14 * 86_400_000
RC4_KEY = b"\xd3"
NOISE_MASK, DATA_MASK = (0x91, 0x42, 0x2C), (0x6E, 0xBD, 0xD3)   # noise expansion: 3 bytes -> 4


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


# ---- SM3 (GB/T 32905) -----------------------------------------------------------------------------------------------
def _rotl(x: int, n: int) -> int:
    n %= 32
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def sm3(data: bytes) -> bytes:
    v = [0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600, 0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E]
    msg = data + b"\x80" + b"\x00" * ((55 - len(data)) % 64) + struct.pack(">Q", len(data) * 8)
    for off in range(0, len(msg), 64):
        w = list(struct.unpack(">16I", msg[off:off + 64]))
        for j in range(16, 68):
            x = w[j - 16] ^ w[j - 9] ^ _rotl(w[j - 3], 15)
            w.append((x ^ _rotl(x, 15) ^ _rotl(x, 23)) ^ _rotl(w[j - 13], 7) ^ w[j - 6])
        w1 = [w[j] ^ w[j + 4] for j in range(64)]
        a, b, c, d, e, f, g, h = v
        for j in range(64):
            t = 0x79CC4519 if j < 16 else 0x7A879D8A
            ss1 = _rotl((_rotl(a, 12) + e + _rotl(t, j)) & 0xFFFFFFFF, 7)
            ss2 = ss1 ^ _rotl(a, 12)
            ff = (a ^ b ^ c) if j < 16 else ((a & b) | (a & c) | (b & c))
            gg = (e ^ f ^ g) if j < 16 else ((e & f) | (~e & g))
            tt1 = (ff + d + ss2 + w1[j]) & 0xFFFFFFFF
            tt2 = (gg + h + ss1 + w[j]) & 0xFFFFFFFF
            d, c, b, a = c, _rotl(b, 9), a, tt1
            h, g, f, e = g, _rotl(f, 19), e, tt2 ^ _rotl(tt2, 9) ^ _rotl(tt2, 17)
        v = [x ^ y for x, y in zip(v, [a, b, c, d, e, f, g, h], strict=True)]
    return struct.pack(">8I", *v)


# ---- bdms primitives ------------------------------------------------------------------------------------------------
def rc4x(key: bytes, data: bytes) -> bytes:
    """bdms's RC4 variant: the S-box starts descending and the key schedule multiplies. Its own inverse."""
    s = list(range(255, -1, -1))
    j = 0
    for i in range(256):
        j = (j * s[i] + j + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def b64(data: bytes, table: str) -> str:
    out = []
    for o in range(0, len(data), 3):
        chunk = data[o:o + 3]
        n = int.from_bytes(chunk + b"\0" * (3 - len(chunk)), "big")
        out += [table[(n >> s) & 63] for s in (18, 12, 6, 0)][:len(chunk) + 1]
    return "".join(out) + "=" * (-len(out) % 4)


def unb64(text: str, table: str) -> bytes:
    index = {c: i for i, c in enumerate(table)}
    body, out = text.rstrip("="), bytearray()
    for o in range(0, len(body), 4):
        chunk = body[o:o + 4]
        n = 0
        for c in chunk:
            n = (n << 6) | index[c]
        n <<= 6 * (4 - len(chunk))
        out += bytes((n >> s) & 255 for s in (16, 8, 0))[:len(chunk) - 1]
    return bytes(out)


def js_bytes(text: str) -> bytes:
    """A JS string as bdms turns it into bytes: one byte per UTF-16 unit below 0x100, else two (big-endian)."""
    out = bytearray()
    raw = text.encode("utf-16-le")
    for k in range(0, len(raw), 2):
        code = raw[k] | (raw[k + 1] << 8)
        if code & 0xFF00:
            out.append(code >> 8)
        out.append(code & 0xFF)
    return bytes(out)


def _mask(pair: tuple[int, int], low: int, high: int) -> list[int]:
    return [(low & 0xAA) | (pair[0] & 0x55), (low & 0x55) | (pair[0] & 0xAA),
            (high & 0xAA) | (pair[1] & 0x55), (high & 0x55) | (pair[1] & 0xAA)]


def _unmask(c: bytes) -> tuple[int, int]:
    return (c[0] & 0x55) | (c[1] & 0xAA), (c[2] & 0x55) | (c[3] & 0xAA)


def _expand(body: bytes, rng: random.Random) -> bytes:
    out = bytearray()
    for o in range(0, len(body), 3):
        g = body[o:o + 3]
        if len(g) < 3:
            out += g
            continue
        n = int(rng.random() * 1000) & 0xFF
        out += bytes((n & m) | (b & d) for m, d, b in zip(NOISE_MASK, DATA_MASK, g, strict=True))
        out.append((g[0] & NOISE_MASK[0]) | (g[1] & NOISE_MASK[1]) | (g[2] & NOISE_MASK[2]))
    return bytes(out)


def _collapse(frame: bytes) -> bytes:
    out = bytearray()
    for o in range(0, len(frame), 4):
        g = frame[o:o + 4]
        if len(g) < 4:
            out += g
            continue
        out += bytes((g[k] & DATA_MASK[k]) | (g[3] & NOISE_MASK[k]) for k in range(3))
    return bytes(out)


def _first_not(digest: bytes, offset: int, sentinel: int, fallback: int) -> int:
    return next((x for x in digest[offset:] if x != sentinel), fallback)


def _double_sm3(data: bytes) -> bytes:
    return sm3(sm3(data + SALT))


def _ua_digest(user_agent: str, env: int, detect: int) -> bytes:
    sealed = rc4x(bytes((env >> 8 & 255, env & 255, detect & 255)), js_bytes(user_agent.strip()))
    return sm3(b64(sealed, S3).encode())


def query_bytes(query: str) -> tuple[int, int, int]:
    """The 3 bytes of the query a signature carries (fields 48, 49, 51)."""
    q = _double_sm3(query.encode())
    return q[9], q[18], _first_not(q, 3, 11, 12)


def body_bytes(body: bytes) -> tuple[int, int, int]:
    """The 3 bytes of the body a signature carries (fields 52, 53, 55)."""
    d = _double_sm3(body)
    return d[10], d[19], _first_not(d, 4, 8, 9)


def ua_bytes(user_agent: str, env: int, detect: int) -> tuple[int, int, int]:
    """The 3 bytes of the User-Agent a signature carries (fields 56, 57, 59); they depend on env and detect."""
    u = _ua_digest(user_agent, env, detect)
    return u[11], u[21], _first_not(u, 5, 12, 13)


def _checksum(version: bytes, fields: Mapping[int, int]) -> int:
    chk = 0
    for b in version:
        chk ^= b
    for k in ORDER:
        chk ^= fields[k]
    return chk


# ---- sign ------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Fingerprint:
    """What a signature claims about the browser. `user_agent` must be the exact `user-agent` header sent."""
    user_agent: str
    screen: str = SCREEN_LINUX_1440
    aid: int = AID
    page_id: int = PAGE_ID


class Signer:
    """Signs requests like one dola page: the first signature carries detect 14, every later one detect 8.

    `clock_ms` and `rng` are injectable so a test gets the same signature twice; production uses the wall clock and
    a fresh `random.Random()`. Not thread-safe (the first-call flag and the rng are shared state)."""

    def __init__(self, fp: Fingerprint, *, clock_ms: Callable[[], int] = _now_ms, rng: random.Random | None = None):
        self.fp = fp
        self._clock_ms = clock_ms
        self._rng = rng or random.Random()
        self._calls = 0

    def sign(self, query: str, body: bytes) -> str:
        """The a_bogus value for this exact query string (without a_bogus) and these exact body bytes."""
        first = self._calls == 0
        self._calls += 1
        return _sign(query, body, self.fp, now=self._clock_ms(), rng=self._rng,
                     detect=DETECT_FIRST if first else DETECT_LATER, nr_tag=NR_TAG_FIRST if first else NR_TAG_LATER)

    def signed_query(self, params: Sequence[tuple[str, str]], body: bytes) -> str:
        """`params` urlencoded in order, then `a_bogus` as the last parameter (dola's page always puts it last)."""
        query = urlencode(list(params))
        tail = urlencode([("a_bogus", self.sign(query, body))])
        return query + "&" + tail if query else tail


def _sign(query: str, body: bytes, fp: Fingerprint, *, now: int, rng: random.Random, detect: int,
          nr_tag: tuple[int, int, int, int]) -> str:
    info, tail = js_bytes(fp.screen), js_bytes("%d," % ((now + 3) & 0xFF))
    f = {24: FIELD_24, 26: (now - EPOCH_MS) // FORTNIGHT_MS & 0xFF, 27: CALL_BUCKET, 28: SINCE_INIT_MS,
         35: ENV & 0xFF, 36: ENV >> 8 & 0xFF, 38: NR & 0xFF, 39: NR >> 8 & 0xFF, 66: TRIPWIRE,
         79: len(info) & 0xFF, 80: len(info) >> 8, 84: len(tail) & 0xFF, 85: len(tail) >> 8}
    f[48], f[49], f[51] = query_bytes(query)
    f[52], f[53], f[55] = body_bytes(body)
    f[56], f[57], f[59] = ua_bytes(fp.user_agent, ENV, detect)
    for k in range(6):
        f[29 + k] = now >> (8 * k) & 0xFF
        f[60 + k] = (now - 1) >> (8 * k) & 0xFF
    for k in range(4):
        f[44 + k] = detect >> (8 * k) & 0xFF
        f[40 + k] = nr_tag[k]
        f[67 + k] = fp.page_id >> (8 * k) & 0xFF
        f[71 + k] = fp.aid >> (8 * k) & 0xFF
    n1 = int(rng.random() * 65535)
    probe = int(rng.random() * 240)
    probe = probe + probe % 2 + 1 if probe > 109 else probe
    trip = (int(rng.random() * 255) & 0x4D) | 0xB2
    version = bytes(_mask(SDK_VERSION[:2], n1 & 0xFF, n1 >> 8) + _mask(SDK_VERSION[2:], probe, trip))
    plain = bytes(f[k] for k in ORDER) + info + tail + bytes((_checksum(version, f),))
    n2 = int(rng.random() * 65535)
    header = bytes(_mask(MAGIC, n2 & 0xFF, int(rng.random() * 40)))
    return b64(header + rc4x(RC4_KEY, version + _expand(plain, rng)), S4)


# ---- decode / verify -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Decoded:
    """One a_bogus taken apart. `*_bytes` are the 3 bytes each input is bound by; `binds_user_agent` is computed
    against the User-Agent given to `decode` (its bytes depend on the decoded env and detect)."""
    length: int
    magic: tuple[int, int]
    version: tuple[int, int, int, int]
    checksum_ok: bool
    now_ms: int
    ink_ms: int
    fortnights: int
    aid: int
    page_id: int
    env: int
    detect: int
    nr: int
    nr_tag: tuple[int, int, int, int]
    call_bucket: int
    since_init_ms: int
    tripwire: int
    field_24: int
    screen: str
    tail: str
    query_bytes: tuple[int, int, int]
    body_bytes: tuple[int, int, int]
    ua_bytes: tuple[int, int, int]
    binds_user_agent: bool
    fields: Mapping[int, int]


def decode(a_bogus: str, *, user_agent: str) -> Decoded:
    """Take an a_bogus apart (ValueError when it is not one: wrong alphabet or too short)."""
    try:
        payload = unb64(a_bogus, S4)
    except KeyError as e:
        raise ValueError("not an a_bogus: character %s outside its alphabet" % e) from None
    plain = rc4x(RC4_KEY, payload[4:])
    version, body = plain[:8], _collapse(plain[8:])
    if len(payload) < 4 or len(body) < len(ORDER):
        raise ValueError("not an a_bogus: %d bytes" % len(payload))
    f = dict(zip(ORDER, body, strict=False))

    def le(first: int, n: int) -> int:
        return sum(f[first + k] << (8 * k) for k in range(n))

    info_len, tail_len = le(79, 2), le(84, 2)
    end = len(ORDER) + info_len + tail_len
    env, detect = le(35, 2), le(44, 4)
    ua = (f[56], f[57], f[59])
    return Decoded(
        length=len(a_bogus), magic=_unmask(payload[:4]), version=_unmask(version[:4]) + _unmask(version[4:8]),
        checksum_ok=end < len(body) and body[end] == _checksum(version, f),
        now_ms=le(29, 6), ink_ms=le(60, 6), fortnights=f[26], aid=le(71, 4), page_id=le(67, 4), env=env,
        detect=detect, nr=le(38, 2), nr_tag=(f[40], f[41], f[42], f[43]), call_bucket=f[27], since_init_ms=f[28],
        tripwire=f[66], field_24=f[24], screen=body[len(ORDER):len(ORDER) + info_len].decode("latin-1"),
        tail=body[len(ORDER) + info_len:end].decode("latin-1"), query_bytes=(f[48], f[49], f[51]),
        body_bytes=(f[52], f[53], f[55]), ua_bytes=ua, binds_user_agent=ua == ua_bytes(user_agent, env, detect),
        fields=f)


def bindings_match(a_bogus: str, query: str, body: bytes, user_agent: str) -> bool:
    """True when this a_bogus binds exactly this query, body and User-Agent (3 bytes each), whatever else it holds;
    False for anything that does not decode."""
    try:
        d = decode(a_bogus, user_agent=user_agent)
    except ValueError:
        return False
    return d.binds_user_agent and d.query_bytes == query_bytes(query) and d.body_bytes == body_bytes(body)


def well_formed(d: Decoded) -> bool:
    """The shape of every signature dola's page made: header, version block, checksum, dola's aid/page id/env/nr
    constants, and a clock that agrees with itself (ink 1–2 ms before now; the tail and the fortnights from now)."""
    return (d.magic == MAGIC and d.version == SDK_VERSION and d.checksum_ok and d.aid == AID and d.page_id == PAGE_ID
            and d.env == ENV and d.nr == NR and d.now_ms - d.ink_ms in (1, 2)
            and d.tail == "%d," % ((d.now_ms + 3) & 0xFF)
            and d.fortnights == (d.now_ms - EPOCH_MS) // FORTNIGHT_MS & 0xFF)


def verify(a_bogus: str, query: str, body: bytes, user_agent: str) -> bool:
    """Strict: the three bindings match (`bindings_match`) AND the signature is well formed (`well_formed`). Any
    structural corruption (version block, checksum, tail, aid...) gives False, as does anything that does not decode."""
    if not bindings_match(a_bogus, query, body, user_agent):
        return False
    return well_formed(decode(a_bogus, user_agent=user_agent))


# ---- the SDK file the page loads -------------------------------------------------------------------------------------
def bdms_changed(page_html: str) -> str | None:
    """None while dola's page still loads `BDMS_SDK_FILE`; else the file name(s) it loads now (or a note that the
    page names no bdms chunk), for an admin alert: a new bdms may change the format.

    The page's inline webpack runtime maps a chunk id to its name (`"9al":"bdms-sdk"`) and to its hash
    (`"9al":"a68c472ba3"`); the file is `<name>.<hash>.js`."""
    names = set()
    for chunk in set(re.findall(r'[{,]"?([\w$]+)"?:"bdms-sdk"', page_html)):
        for digest in re.findall(r'[{,]"?%s"?:"([0-9a-f]{6,})"' % re.escape(chunk), page_html):
            names.add("bdms-sdk.%s.js" % digest)
    if names == {BDMS_SDK_FILE}:
        return None
    return ", ".join(sorted(names)) or "no bdms-sdk chunk in the page"
