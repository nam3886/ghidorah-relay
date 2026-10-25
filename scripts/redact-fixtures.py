"""Turn raw spike captures into committable test fixtures.

Values are replaced, keys never. A replaced value keeps its type, its length and its character
classes, and the same input always gives the same fake (salted HMAC), so equality survives: a user
id in a chain still equals the same id in its messages. JSON nested inside strings, base64-wrapped
URLs (dola `main_url`) and signed URL query strings are all handled.

    redact-fixtures.py redact SRC DST          SRC = ~/relay-secrets/raw
    redact-fixtures.py check DST [--raw SRC]   exit 1 on a leak; --raw also greps every original value
    redact-fixtures.py selftest

The salt lives in ~/relay-secrets/redact-salt (0600) and never leaves the machine.
"""
import base64
import glob
import hashlib
import hmac
import json
import os
import re
import sys

SECRETS_DIR = os.path.expanduser("~/relay-secrets")
SALT_PATH = os.path.join(SECRETS_DIR, "redact-salt")

# Keys whose value identifies a person, a device or a session (compared lowercased).
SECRET_KEYS = {
    "user_id", "userid", "abrauserid", "kadabrauserid", "kadabraprofileid", "kadabraprofileusername",
    "kadabraprofilepictureurl", "username", "user_name", "nick_name", "creator_id", "creator_username",
    "sender_id", "sec_sender", "receiver_id", "sessionid", "fetch_token", "device_id", "web_id",
    "inner_did", "inner_tea_uuid", "inner_tt_wid", "inner_ttwid", "inner_user_ip", "inner_webid",
    "tt", "tt_stable", "share_url", "qr_code_url", "email", "phone", "cookie", "set-cookie",
    "authorization",
}
NAME_KEYS = {"user_name", "nick_name"}      # these also hold the bot's own name, which stays
IDENTITY_MARKERS = {"username", "kadabraprofileusername", "kadabrauserid"}   # a dict with one: its "id" is a person
# URL query parameters whose value is kept; every other value becomes x's of the same length.
KEEP_QS = {"mime_type", "codec_type", "stream_type", "download", "device_platform", "aid", "logo_type",
           "_nc_cat", "_nc_ht", "br", "bt", "ft", "qs", "vps"}

URL_RE = re.compile(r"(?:https?://|/)[^\s\"'<>\\]*\?[^\s\"'<>\\]+")
B64_RE = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_KEY_ALT = "|".join(sorted(map(re.escape, SECRET_KEYS), key=len, reverse=True))
# "key":"value" / \"key\":\"value\" / "key":123 inside text that is not itself JSON (WS frames)
EMBEDDED_STR = re.compile(r'(?i)(\\*"(?:%s)\\*"\s*:\s*\\*")([^"\\]*)' % _KEY_ALT)
EMBEDDED_NUM = re.compile(r'(?i)(\\*"(?:%s)\\*"\s*:\s*)(-?\d+)' % _KEY_ALT)


def load_salt() -> bytes:
    if not os.path.exists(SALT_PATH):
        fd = os.open(SALT_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(os.urandom(32).hex())
    return open(SALT_PATH).read().strip().encode()


class Redactor:
    def __init__(self, salt: bytes, literals=(), keep=()):
        self.salt = salt
        self.keep = set(keep)                      # exact values never replaced (the bot's name)
        lits = sorted({x for x in literals if x and x not in self.keep and len(x) >= 6}, key=len, reverse=True)
        parts = [r"(?<!\d)%s(?!\d)" % re.escape(x) if x.isdigit() else re.escape(x) for x in lits]
        self.literal_re = re.compile("|".join(parts)) if parts else None
        self.literal_set = set(lits)

    # ---- fakes --------------------------------------------------------------------------------
    def fake_str(self, s: str) -> str:
        if s in self.keep:
            return s
        stream, i, out = hmac.new(self.salt, s.encode(), hashlib.sha256).digest(), 0, []
        for ch in s:
            if i >= len(stream):
                stream += hmac.new(self.salt, stream[-32:], hashlib.sha256).digest()
            b = stream[i]
            i += 1
            if ch.isdigit():
                out.append(str(b % 10))
            elif "a" <= ch <= "z":
                out.append(chr(97 + b % 26))
            elif "A" <= ch <= "Z":
                out.append(chr(65 + b % 26))
            else:
                out.append(ch)                     # separators keep the shape (uuid, ip, url)
        if s[:1].isdigit() and s.isdigit() and out[0] == "0":
            out[0] = "1"
        return "".join(out)

    def fake(self, v):
        if isinstance(v, bool) or v is None or isinstance(v, float):
            return v
        if isinstance(v, int):
            return int(("-" if v < 0 else "") + self.fake_str(str(abs(v))))
        if isinstance(v, str):
            if v.startswith("http"):           # keep it a URL: host kept, path faked, query x'd
                head, q, query = v.partition("?")
                host, path = re.match(r"(https?://[^/]*)(.*)", head).groups()
                head = host + self.fake_str(path)
                return self.url(head + q + query) if q else head
            return self.fake_str(v)
        return v

    # ---- walkers ------------------------------------------------------------------------------
    def value(self, v, key: str = ""):
        k = key.lower()
        if isinstance(v, dict):
            person = bool(IDENTITY_MARKERS & {x.lower() for x in v})
            return {kk: (self.fake(x) if person and kk == "id" and not isinstance(x, (dict, list))
                         else self.value(x, kk)) for kk, x in v.items()}
        if isinstance(v, list):
            return [self.value(x, key) for x in v]
        if k in SECRET_KEYS and not isinstance(v, (dict, list)):
            return v if (k in NAME_KEYS and v in self.keep) else self.fake(v)
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, int) and not isinstance(v, bool) and str(v) in self.literal_set:
            return self.fake(v)                # a known id as a number under an ordinary key
        return v

    def text(self, s: str) -> str:
        if s[:1] in "{[":
            try:
                obj = json.loads(s)
            except ValueError:
                obj = None
            if isinstance(obj, (dict, list)):
                return json.dumps(self.value(obj), ensure_ascii=False, separators=(",", ":"))
        if s.startswith("aHR0c") and B64_RE.fullmatch(s):      # base64 of "http…"
            codecs = ((base64.b64decode, base64.b64encode), (base64.urlsafe_b64decode, base64.urlsafe_b64encode))
            for dec, enc in codecs:
                try:
                    raw = dec(s + "=" * (-len(s) % 4)).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    continue
                out = enc(self.text(raw).encode()).decode()
                return out if s.endswith("=") else out.rstrip("=")
        s = URL_RE.sub(lambda m: self.url(m.group(0)), s)
        s = EMBEDDED_STR.sub(lambda m: m.group(1) + self.fake_str(m.group(2)), s)
        s = EMBEDDED_NUM.sub(lambda m: m.group(1) + str(self.fake(int(m.group(2)))), s)
        if self.literal_re:
            s = self.literal_re.sub(lambda m: self.fake_str(m.group(0)), s)
        return s

    @staticmethod
    def url(u: str) -> str:
        head, _, query = u.partition("?")

        def one(pair: str) -> str:
            k, eq, v = pair.partition("=")
            return pair if (not eq or k in KEEP_QS) else "%s=%s" % (k, "x" * len(v))
        return head + "?" + "&".join(one(p) for p in query.split("&"))


# ---- sources ----------------------------------------------------------------------------------
def cookie_values() -> list:
    out = []
    for f in glob.glob(os.path.join(SECRETS_DIR, "*.json")):
        try:
            out += [c.get("value", "") for c in json.load(open(f)).get("cookies", [])]
        except (ValueError, AttributeError):
            pass
    return [v for v in out if len(v) >= 8]


def collect_identities(objs) -> tuple:
    """(values found under SECRET_KEYS, the bot's own names) — the literal list for substring hits."""
    found, bots = set(), set()

    def walk(v, key=""):
        if isinstance(v, dict):
            person = bool(IDENTITY_MARKERS & {x.lower() for x in v})
            for kk, x in v.items():
                if person and kk == "id" and isinstance(x, (str, int)):
                    found.add(str(x))
                walk(x, kk)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, str) and v[:1] in "{[":
            try:
                walk(json.loads(v), key)
            except ValueError:
                pass
        elif key.lower() == "bot_name" and isinstance(v, str):
            bots.add(v)
        elif key.lower() in SECRET_KEYS and isinstance(v, (str, int)) and not isinstance(v, bool):
            found.add(str(v))
    for o in objs:
        walk(o)
    return found - bots, bots


# Where each raw capture goes, and the one line its README entry says.
LAYOUT = [
    ("spike_s1/*.json", "dola/s1-network",
     "dola page network during one send (http /im/chain/single + ws), 30/09 spike S1"),
    ("dola_chains/*.json", "dola/chains",
     "one /im/chain/single read of a real job's conversation, by case; box, 30/09"),
    ("spike_s3/*.json", "meta/s3-network",
     "meta.ai page network for one prompt (graphql http + gateway ws), 30/09 spike S3"),
    ("spike_s4/*.json", "vibes/s4-capture",
     "every vibes response: auth, t2i, t2v write-ahead + resume polls, a never-generated shell; 30/09"),
]

# Fixtures made by hand (not from a raw capture); listed in fixtures/README.md after the captured ones.
HAND_MADE = [
    ("dola/listing-capped-30.json",
     "synthetic: a full 30-item conversation listing with one unknown conversation that does not carry the job's text "
     "(the capped-listing crash window)"),
    ("dola/action_bar_conf.json",
     "the dola action-bar config response (`get_item_conf`) the 30 s patch reads, from earlier samples"),
]

# Every fixture gap; written into fixtures/README.md.
GAPS = [
    "dola watermark-only: none of the 38 real done videos (30/09) lacked `main_url`. Tests build it from a done "
    "chain by deleting `main_url` from its `video_model` (derived, not captured).",
    "dola throttle SSE (`/chat/completion` `error_code`) and dola likeness refusal: not captured here. The "
    "SSE bodies in `dola/sse/*.txt` are reconstructed from the event names and error codes of earlier samples (not "
    "live captures); a live 710022004 body replaces them when one is measured.",
    "vibes error bodies (401, 403, 429, QUOTA, a rejected generate, a refused image edit) and an extend past its "
    "~16.7 s ceiling: not captured yet.",
    "meta refusal, rate or limit text, text-only reply and logged-out page: not captured yet.",
]


def redact_tree(src: str, dst: str) -> None:
    files = [(f, sub, note) for pat, sub, note in LAYOUT for f in sorted(glob.glob(os.path.join(src, pat)))]
    objs = [json.load(open(f)) for f, _s, _n in files]
    ids, bots = collect_identities(objs)
    r = Redactor(load_salt(), literals=list(ids) + cookie_values(), keep=bots)
    readme = ["# Fixtures (redacted)", "",
              "Made by `scripts/redact-fixtures.py` from raw spike captures. Values replaced, keys kept.", ""]
    for (f, sub, note), obj in zip(files, objs, strict=True):
        os.makedirs(os.path.join(dst, sub), exist_ok=True)
        rel = os.path.join(sub, os.path.basename(f))
        with open(os.path.join(dst, rel), "w", encoding="utf-8") as fh:
            json.dump(r.value(obj), fh, ensure_ascii=False, indent=1)
        readme.append("- `%s` — %s." % (rel, note))
    readme += ["- `%s` — %s." % h for h in HAND_MADE]
    readme += ["", "## Gaps", "",
               "What the captures do not hold.", ""]
    readme += ["- " + g for g in GAPS]
    open(os.path.join(dst, "README.md"), "w").write("\n".join(readme) + "\n")
    print("redacted %d files, %d identity values, %d bot names kept" % (len(files), len(ids), len(bots)))


# ---- check ------------------------------------------------------------------------------------
def leaks(dst: str, raw: str = "") -> list:
    texts = {f: open(f, encoding="utf-8").read() for f in glob.glob(os.path.join(dst, "**/*.json"), recursive=True)}
    bad = []
    for f, t in texts.items():
        # the same JSON string can be escaped several times over; unescape a few rounds to see URLs
        flat = t
        for _ in range(4):
            flat = flat.replace('\\"', '"').replace("\\\\", "\\").replace("\\u0026", "&").replace("\\/", "/")
        for u in URL_RE.findall(flat):
            for pair in u.partition("?")[2].split("&"):
                k, eq, v = pair.partition("=")
                if eq and k not in KEEP_QS and v.strip("x"):
                    bad.append((f, "query %s not redacted" % k))
        for m in re.finditer(r"(?i)\"(cookie|set-cookie|authorization)\"\s*:\s*\"([^\"]+)\"", flat):
            bad.append((f, "%s header present" % m.group(1)))
    if raw:
        objs = [json.load(open(f)) for pat, _s, _n in LAYOUT for f in glob.glob(os.path.join(raw, pat))]
        ids, _bots = collect_identities(objs)
        for v in sorted(set(ids) | set(cookie_values())):
            if len(v) >= 6:
                why = "original identity/cookie value survives (len %d)" % len(v)
                bad += [(f, why) for f, t in texts.items() if v in t]
    return bad


def selftest() -> None:
    r = Redactor(b"k", literals=["Nguyen Van A", "7412345678"], keep=["Dola"])
    doc = {"user_id": 7412345678, "nick_name": "Dola", "user_name": "Nguyen Van A",
           "url": "https://cdn.x/a.jpg?oh=abc123&oe=FF00&mime_type=image/png",
           "content": json.dumps({"text": "hi Nguyen Van A", "sender_id": "741"}),
           "main_url": base64.b64encode(b"https://v.x/v.mp4?x-signature=SIG123").decode(),
           "ws": '{\\"user_id\\":\\"998877\\"}', "material": json.dumps({"creator": 7412345678}),
           "kadabraProfilePictureURL": "https://p.x/a/b.jpg?oh=abc&_nc_cat=1"}
    out = r.value(doc)
    assert out["nick_name"] == "Dola" and out["user_name"] != "Nguyen Van A" and len(out["user_name"]) == 12
    assert isinstance(out["user_id"], int) and len(str(out["user_id"])) == 10 and out["user_id"] != 7412345678
    assert out["url"] == "https://cdn.x/a.jpg?oh=xxxxxx&oe=xxxx&mime_type=image/png"
    assert "Nguyen Van A" not in out["content"] and '"sender_id":"741"' not in out["content"]
    assert base64.b64decode(out["main_url"]).decode().endswith("x-signature=xxxxxx")
    assert "998877" not in out["ws"]
    assert "7412345678" not in out["material"]
    pic = out["kadabraProfilePictureURL"]
    assert pic.startswith("https://") and pic.endswith("?oh=xxx&_nc_cat=1") and "/a/b.jpg" not in pic
    assert r.fake("Nguyen Van A") == r.fake("Nguyen Van A") == out["user_name"]   # equality survives
    print("selftest ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if cmd == "redact":
        redact_tree(sys.argv[2], sys.argv[3])
    elif cmd == "check":
        raw = sys.argv[sys.argv.index("--raw") + 1] if "--raw" in sys.argv else ""
        found = leaks(sys.argv[2], raw)
        for f, why in found[:30]:
            print("LEAK", os.path.relpath(f, sys.argv[2]), why)
        print("check: %d leaks" % len(found))
        sys.exit(1 if found else 0)
    else:
        selftest()
