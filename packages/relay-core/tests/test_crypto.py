"""Credential sealing (contracts §13, I20 crypto part): a process holding only the public key cannot open a blob."""

import os
import subprocess
import sys

import pytest

from relay_core import crypto
from relay_core.crypto import CryptoError, generate_keypair, open_blob, seal
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/crypto.py"
SECRET = b'{"cookies": [{"name": "sessionid", "value": "SECRET-COOKIE-VALUE"}]}'


def test_round_trip_and_the_blob_hides_the_plaintext():
    keys = generate_keypair()
    blob = seal(keys.public, SECRET)
    assert open_blob(blob, [keys.private]) == SECRET
    assert b"SECRET-COOKIE-VALUE" not in blob and b"sessionid" not in blob
    assert seal(keys.public, SECRET) != blob                       # a fresh ephemeral key per seal
    assert crypto.blob_key_id(blob) == keys.key_id
    assert open_blob(seal(keys.public, b""), [keys.private]) == b""
    big = os.urandom(200_000)
    assert open_blob(seal(keys.public, big), [keys.private]) == big


@mutation(SRC, "return _MAGIC + bytes([len(key_id)]) + key_id + SealedBox(PublicKey(raw)).encrypt(plaintext)",
          "return _MAGIC + bytes([len(key_id)]) + key_id + plaintext")
def test_sealing_encrypts():
    keys = generate_keypair()
    blob = seal(keys.public, SECRET)
    assert SECRET not in blob
    assert open_blob(blob, [keys.private]) == SECRET


def test_a_process_with_only_the_public_key_can_seal_but_not_open(tmp_path):
    keys = generate_keypair()
    script = (
        "import sys\n"
        "from relay_core import crypto\n"
        "blob = crypto.seal(crypto.public_key_from_env(), b'planted-by-the-api')\n"
        "open('%s', 'wb').write(blob)\n"
        "try:\n"
        "    crypto.open_blob(blob, crypto.private_keys_from_env())\n"
        "except crypto.CryptoError as exc:\n"
        "    print('CANNOT-OPEN', exc)\n"
        "    sys.exit(0)\n"
        "sys.exit('the api-side process opened a sealed blob')\n" % (tmp_path / "blob")
    )
    env = {k: v for k, v in os.environ.items() if k != crypto.PRIVATE_KEY_VAR}
    env[crypto.PUBLIC_KEY_VAR] = keys.public
    done = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "CANNOT-OPEN" in done.stdout and crypto.PRIVATE_KEY_VAR in done.stdout
    # the worker, which holds the private key, opens what the api sealed
    assert open_blob((tmp_path / "blob").read_bytes(), [keys.private]) == b"planted-by-the-api"


def test_env_helpers():
    keys = generate_keypair()
    assert crypto.public_key_from_env({crypto.PUBLIC_KEY_VAR: " %s " % keys.public}) == keys.public
    with pytest.raises(CryptoError, match="not set"):
        crypto.public_key_from_env({})
    with pytest.raises(CryptoError, match="cannot open credentials"):
        crypto.private_keys_from_env({crypto.PUBLIC_KEY_VAR: keys.public})
    assert crypto.private_keys_from_env({crypto.PRIVATE_KEY_VAR: "a, b,,c"}) == ["a", "b", "c"]


@mutation(SRC, "if key_id_of(bytes(private.public_key)) == wanted:", "if True:")
def test_rotation_picks_the_key_that_sealed_the_blob():
    old, new = generate_keypair(), generate_keypair()
    assert old.key_id != new.key_id
    blob = seal(old.public, SECRET)
    assert open_blob(blob, [new.private, old.private]) == SECRET     # the order of the keys does not matter
    assert crypto.blob_key_id(seal(new.public, SECRET)) == new.key_id


def test_wrong_key_and_damaged_blobs_are_refused():
    keys, other = generate_keypair(), generate_keypair()
    blob = seal(keys.public, SECRET)
    with pytest.raises(CryptoError, match="no private key"):
        open_blob(blob, [other.private])
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(CryptoError, match="corrupt"):
        open_blob(tampered, [keys.private])
    for junk in (b"", b"rs", b"rs1", b"xxx" + blob[3:], blob[:3] + bytes([200]) + blob[4:12]):
        with pytest.raises(CryptoError):
            open_blob(junk, [keys.private])


def test_malformed_keys_are_refused():
    keys = generate_keypair()
    for bad in ("", "not base64 !!", "AAAA", "é"):
        with pytest.raises(CryptoError):
            seal(bad, b"x")
        with pytest.raises(CryptoError):
            open_blob(seal(keys.public, b"x"), [bad])


def test_a_keypair_does_not_print_its_private_half():
    keys = generate_keypair()
    assert keys.private not in repr(keys) and keys.public in repr(keys)
