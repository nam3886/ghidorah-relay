"""The a_bogus signer for the plain-HTTP send: dola refuses every send whose signature does not bind the exact query,
body and User-Agent, or does not look like its page's own (FINDINGS "HTTP send spike 23/10"). No network."""

import json
import random
from pathlib import Path
from urllib.parse import unquote

from relay_core.testing.mutation import mutation
from relay_dola.signing import (
    BDMS_SDK_FILE,
    SCREEN_LINUX_1440,
    Fingerprint,
    Signer,
    abogus,
    bdms_changed,
    bindings_match,
    decode,
    verify,
    well_formed,
)

SIGNER_FILE = "packages/provider-dola/src/relay_dola/signing/abogus.py"
PAGE = json.loads((Path(__file__).resolve().parents[3] / "fixtures" / "dola" / "api-spike-261023"
                   / "page_signatures.json").read_text(encoding="utf-8"))

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
# the send's query shape with fake ids, and a body with non-ASCII text (hashed as its UTF-8 bytes)
QUERY = ("version_code=20800&language=en&device_platform=web&doubao_device_platform=web&aid=495671&real_aid=495671"
         "&pkg_type=release_version&device_id=&pc_version=3.42.0&doubao_pc_version=3.42.0&web_id=7000000000000000001"
         "&tea_uuid=7000000000000000001&region=VN&sys_region=VN&samantha_web=1&web_platform=browser"
         "&use-olympus-account=1&web_tab_id=00000000-0000-4000-8000-000000000000&tz_name=Asia%2FHo_Chi_Minh")
BODY = '{"messages":[{"content":"Generate image: một con mèo ngủ trên mái nhà"}]}'.encode()
# Made by the spike's own abogus.py on the box (the signer that got 3 paid sends accepted on 23/10) for these inputs,
# one random.Random(1) shared by the three calls, detect 14 then 8 then 8, at these clock values.
CLOCK = [1_792_000_000_123, 1_792_000_000_050, 1_792_000_000_252]
GOLDEN = [
    "dy0Vg77wDoAjadFtYCnCey/5ohWMNTSypaT/WEoR9oP/L7zbUJpVtKawnFTy4QCbf8shNF1H6DzabDVPqW42VMrpomkDSDiRn8oAVWmLZqwgb0JsgH"
    "bTCw0FKwsKWbGua/5Ji1RX8UGqZE55iHQm8Q5HL/lr-Qm2Br-tfMucJ9WTVAfoNIO8FFvmYwjhBGI3OtwO7p-N",
    "Y7sRkeS7doQVadMSuCitS6M57XClNP8yqFioWEo5CoTly7UbGiBAtqewcFYLsRCYyRkwNF1HcdPcbVnPp8v2V/9kFmkkSLzfVWK5VXfLMqNhbzksLr"
    "jTCL8zLwBC8bGu-AV4i1WXgUa9ZdO5kNdu8Q-Hu/ln-Ob2Pq3GfZzcG9AGVCEopVx8FKvmTwngBa23PtifHhm=",
    "mj0nDFSjY2AfadFGYKiXSW/Rg7/MNP8y6aTdbtp5HqOgyXUGGiBVtOCqrou74cKYy8kwNqVHGdPMbjVcNm4k3ZrpzmkfSmhRn8o9VX8o/qqXbzJsDq"
    "jiCzmFowsF8cGu-55XiIWX2Ua91E55hqQu8pl7u/Un-QY2Fr3vfMYcJ9WGVSjoZVOuFofmiwjh-a23/sGw7r5v",
]
GOLDEN_INPUTS = [(QUERY, BODY), (QUERY, BODY), ("a=1", b"")]


def signer(seed=1, clock=None):
    ticks = iter(clock or CLOCK)
    return Signer(Fingerprint(UA), clock_ms=lambda: next(ticks), rng=random.Random(seed))


def shape(d):
    """What must look like the page's own signature, whatever the inputs and the clock."""
    return (d.length, d.magic, d.version, d.aid, d.page_id, d.env, d.nr, d.call_bucket, d.tripwire, d.field_24,
            d.screen, d.ink_ms - d.now_ms, d.tail == "%d," % ((d.now_ms + 3) & 255),
            d.fortnights == (d.now_ms - abogus.EPOCH_MS) // abogus.FORTNIGHT_MS, (d.detect, d.nr_tag))


def test_sm3_matches_the_standard_vectors():
    assert abogus.sm3(b"abc").hex() == "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    assert abogus.sm3(b"abcd" * 16).hex() == "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732"


@mutation(SIGNER_FILE, 'SALT = b"dhzx"', 'SALT = b"dhzy"', expect="body binding")
@mutation(SIGNER_FILE, "return d[10], d[19], _first_not(d, 4, 8, 9)", "return d[10], d[18], _first_not(d, 4, 8, 9)",
          expect="body binding")
@mutation(SIGNER_FILE, "bytes((env >> 8 & 255, env & 255, detect & 255))", "bytes((env >> 8 & 255, env & 255, 14))",
          expect="user-agent binding")
@mutation(SIGNER_FILE, "61, 73, 29, 70,", "61, 70, 29, 73,", expect="field order")
def test_every_page_signature_decodes_and_binds_its_recorded_inputs():
    """92 signatures dola's own page made (four page loads, the box Chrome 154). The fixture holds what each binds,
    computed on the box from the raw query/body/UA, and the body itself where it holds no account id. No query text:
    each holds a tab id (most an account's web_id too), so the query hashing is proven by the spike reference test."""
    assert len(PAGE) == 92
    shapes, checksum_bad, bodies = set(), [], 0
    for i, row in enumerate(PAGE):
        d = decode(row["a_bogus"], user_agent=row["user_agent"])
        assert d.aid == 495671 and d.page_id == 26930 and d.version == (1, 0, 1, 0), "field order: #%d %r" % (i, d)
        shapes.add(shape(d))
        assert list(d.query_bytes) == row["bound"]["query"], "query binding: #%d" % i
        assert list(d.body_bytes) == row["bound"]["body"], "body binding: #%d" % i
        assert d.binds_user_agent and list(d.ua_bytes) == row["bound"]["user_agent"], "user-agent binding: #%d" % i
        if row["body"] is not None:
            bodies += 1
            assert abogus.body_bytes(row["body"].encode()) == d.body_bytes, "body binding: #%d recomputed" % i
        assert well_formed(d) == d.checksum_ok, "well formed: #%d" % i
        if not d.checksum_ok:
            checksum_bad.append((row["source"], d.tail))
    assert bodies == 84, "page bodies on file to recompute: %d" % bodies
    # FINDINGS: one page signature lacks its checksum byte (a 4-character tail, one byte short) yet binds everything
    assert checksum_bad == [("capture-0630-nochip", "171,")], checksum_bad
    common = {s[:-1] for s in shapes}
    assert common == {(200, (3, 82), (1, 0, 1, 0), 495671, 26930, 1, 1, 6, 3, 41, SCREEN_LINUX_1440, -1, True, True),
                      (200, (3, 82), (1, 0, 1, 0), 495671, 26930, 1, 1, 6, 3, 41, SCREEN_LINUX_1440, -2, True, True)}
    assert {s[-1] for s in shapes} == {(14, (0, 0, 0, 0)), (8, (1, 1, 1, 0))}


@mutation(SIGNER_FILE, "return q[9], q[18], _first_not(q, 3, 11, 12)", "return q[9], q[17], _first_not(q, 3, 11, 12)",
          expect="spike reference")
@mutation(SIGNER_FILE, 'RC4_KEY = b"\\xd3"', 'RC4_KEY = b"\\xd4"', expect="spike reference")
@mutation(SIGNER_FILE, "AID = 495671", "AID = 495672", expect="spike reference")
@mutation(SIGNER_FILE, "PAGE_ID = 26930", "PAGE_ID = 26931", expect="spike reference")
@mutation(SIGNER_FILE, "MAGIC = (3, 82)", "MAGIC = (3, 83)", expect="spike reference")
@mutation(SIGNER_FILE, "TRIPWIRE = 3", "TRIPWIRE = 4", expect="spike reference")
@mutation(SIGNER_FILE, '"%d," % ((now + 3) & 0xFF)', '"%d," % ((now + 2) & 0xFF)', expect="spike reference")
def test_signatures_match_the_spike_reference_byte_for_byte():
    s = signer()
    for i, ((query, body), want) in enumerate(zip(GOLDEN_INPUTS, GOLDEN, strict=True)):
        assert s.sign(query, body) == want, "spike reference: call %d differs" % i


@mutation(SIGNER_FILE, "first = self._calls == 0", "first = True", expect="detect")
def test_the_first_signature_carries_detect_14_and_every_later_one_detect_8():
    s = signer(clock=[1_792_000_000_000 + k for k in range(4)])
    got = [decode(s.sign(QUERY, BODY), user_agent=UA) for _ in range(4)]
    assert [(d.detect, d.nr_tag) for d in got] == [(14, (0, 0, 0, 0))] + [(8, (1, 1, 1, 0))] * 3, "detect"
    assert all(d.binds_user_agent for d in got), "the UA bytes follow detect"
    assert decode(Signer(Fingerprint(UA)).sign("a=1", b""), user_agent=UA).detect == 14, "detect: per Signer"


def test_ours_decode_like_the_pages_and_verify_their_inputs():
    page_shapes = {shape(decode(r["a_bogus"], user_agent=r["user_agent"])) for r in PAGE}
    s = Signer(Fingerprint(UA))   # wall clock, fresh rng
    for query, body in [(QUERY, BODY), ("a=1", b""), ("", b"{}")] * 3:
        a_bogus = s.sign(query, body)
        d = decode(a_bogus, user_agent=UA)
        assert d.checksum_ok and shape(d) in page_shapes, d
        assert verify(a_bogus, query, body, UA)
        assert not verify(a_bogus, query + "x", body, UA), "a changed query still verifies"
        assert not verify(a_bogus, query, body + b" ", UA), "a changed body still verifies"
        assert not verify(a_bogus, query, body, UA.replace("154", "155")), "another UA still verifies"
    assert not verify("not!base64", QUERY, BODY, UA) and not verify("", QUERY, BODY, UA)


def test_same_clock_and_seed_give_the_same_signature():
    assert signer(seed=7).sign(QUERY, BODY) == signer(seed=7).sign(QUERY, BODY)
    other = signer(seed=8).sign(QUERY, BODY)
    assert other != signer(seed=7).sign(QUERY, BODY) and verify(other, QUERY, BODY, UA)
    fp = Fingerprint(UA, screen="1920|1080|1920|1040|1920|1080|1920|1080|Linux x86_64", aid=1, page_id=2)
    d = decode(Signer(fp).sign("a=1", b""), user_agent=UA)
    assert (d.screen, d.aid, d.page_id) == (fp.screen, 1, 2)


@mutation(SIGNER_FILE, 'return query + "&" + tail if query else tail', 'return tail + "&" + query if query else tail',
          expect="last")
def test_signed_query_keeps_the_order_and_puts_a_bogus_last():
    params = [("version_code", "20800"), ("device_id", ""), ("tz_name", "Asia/Ho_Chi_Minh"), ("aid", "495671")]
    out = signer().signed_query(params, BODY)
    query, _, tail = out.rpartition("&a_bogus=")
    assert query == "version_code=20800&device_id=&tz_name=Asia%2FHo_Chi_Minh&aid=495671", "order or last: %s" % out
    assert tail and "&" not in tail and verify(unquote(tail), query, BODY, UA), "a_bogus is not last or does not bind"
    alone = signer().signed_query([], b"")
    assert alone.startswith("a_bogus=") and verify(unquote(alone[8:]), "", b"", UA)


def runtime(names: str, hashes: str) -> str:
    """The two maps of dola's inline webpack runtime, as the /chat page served them on 23/10."""
    return ('<script>var c={"auto-switch-write-skill-url-parse-plugin":{"chunkIds":["wxi"]},"bdms-sdk":{"chunkIds":'
            '["9al"]}};n.u=e=>"static/js/async/"+({%s}[e]||e)+"."+{%s}[e]+".js"</script>' % (names, hashes))


@mutation(SIGNER_FILE, "if names == {BDMS_SDK_FILE}:", "if names:", expect="new bdms")
def test_bdms_changed_names_the_new_sdk_file():
    names = '"95g":"s2-cron-jobs-page",96:"s2-miaoda-code-runner-sdk","9al":"bdms-sdk","9b7":"async-upload"'
    known = runtime(names, '96:"0dcbc31cad","9a":"c829d1f008","9al":"a68c472ba3","9b":"dbb016a03c"')
    assert BDMS_SDK_FILE == "bdms-sdk.a68c472ba3.js" and bdms_changed(known) is None
    assert bdms_changed(known.replace("a68c472ba3", "0123456789")) == "bdms-sdk.0123456789.js", "new bdms"
    assert bdms_changed(runtime(names.replace('"9al"', "97"), '97:"a68c472ba3","9al":"ffffffffff"')) is None
    assert bdms_changed("<html></html>") == "no bdms-sdk chunk in the page"


def reframe(a_bogus, edit, *, fix_checksum=True):
    """Re-encode a signature after `edit(version, body)` changed its decrypted bytes (the checksum is the body's last
    byte on ours), keeping the checksum right unless told not to."""
    payload = abogus.unb64(a_bogus, abogus.S4)
    plain = abogus.rc4x(abogus.RC4_KEY, payload[4:])
    version, body = bytearray(plain[:8]), bytearray(abogus._collapse(plain[8:]))
    edit(version, body)
    if fix_checksum:
        body[-1] = abogus._checksum(bytes(version), dict(zip(abogus.ORDER, body, strict=False)))
    frame = bytes(version) + abogus._expand(bytes(body), random.Random(0))
    return abogus.b64(payload[:4] + abogus.rc4x(abogus.RC4_KEY, frame), abogus.S4)


def _flip(field, bit=1):
    def edit(_version, body):
        body[abogus.ORDER.index(field)] ^= bit
    return edit


def _tail(_version, body):
    k = len(abogus.ORDER) + len(SCREEN_LINUX_1440)
    body[k] = ord("8") if body[k] != ord("8") else ord("9")


def _version(version, _body):
    version[0] ^= 0x01


def _checksum(_version, body):
    body[-1] ^= 1


@mutation(SIGNER_FILE, "    return well_formed(decode(a_bogus, user_agent=user_agent))", "    return True",
          expect="corruption")
@mutation(SIGNER_FILE, "d.version == SDK_VERSION and d.checksum_ok", "d.checksum_ok", expect="version corruption")
@mutation(SIGNER_FILE, "and d.checksum_ok and d.aid == AID", "and d.aid == AID", expect="checksum corruption")
@mutation(SIGNER_FILE, "d.aid == AID and d.page_id == PAGE_ID", "d.page_id == PAGE_ID", expect="aid corruption")
@mutation(SIGNER_FILE, 'and d.tail == "%d," % ((d.now_ms + 3) & 0xFF)', "and True", expect="tail corruption")
@mutation(SIGNER_FILE, "d.now_ms - d.ink_ms in (1, 2)", "True", expect="clock corruption")
def test_verify_refuses_a_signature_whose_structure_is_corrupted_though_its_bindings_hold():
    good = signer().sign(QUERY, BODY)
    assert verify(reframe(good, lambda v, b: None), QUERY, BODY, UA), "the re-encoder itself breaks signatures"
    cases = [("version", _version, True), ("checksum", _checksum, False), ("aid", _flip(71), True),
             ("page id", _flip(67), True), ("tail", _tail, True), ("clock", _flip(60, 4), True)]
    for name, edit, fix in cases:
        bad = reframe(good, edit, fix_checksum=fix)
        assert bindings_match(bad, QUERY, BODY, UA), "%s: the corruption touched a binding" % name
        assert not verify(bad, QUERY, BODY, UA), "%s corruption still verifies" % name
