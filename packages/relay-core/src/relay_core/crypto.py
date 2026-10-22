"""Credential envelope encryption (I20): a NaCl sealed box.

The API holds the public key only: it can seal a credential but never open one. Workers hold the private key.
A blob is `b"rs1" + len(key_id) + key_id + sealed`; the key id (a hash of the public key) names the key that sealed
it, so a rotation is a new key pair plus lazy re-encryption: workers hold old and new private keys and `open_blob`
picks the right one.

Keys travel as url-safe base64 text (env-friendly). `relay keygen` (a later phase) calls `generate_keypair`.
"""

import base64
import binascii
import hashlib
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from nacl.exceptions import CryptoError as _NaClError
from nacl.public import PrivateKey, PublicKey, SealedBox

PUBLIC_KEY_VAR = "CREDENTIAL_SEAL_PUBLIC_KEY"
PRIVATE_KEY_VAR = "CREDENTIAL_SEAL_PRIVATE_KEY"      # workers only; a comma-separated list during a rotation
_MAGIC = b"rs1"


class CryptoError(Exception):
    """A key is missing or malformed, or a blob cannot be opened with the keys given."""


@dataclass(frozen=True)
class KeyPair:
    key_id: str
    public: str
    private: str = field(repr=False)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_key(text: str, what: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(text.strip().encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise CryptoError("the %s key is not valid base64" % what) from None
    if len(raw) != 32:
        raise CryptoError("the %s key must be 32 bytes" % what)
    return raw


def key_id_of(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:12]


def generate_keypair() -> KeyPair:
    private = PrivateKey.generate()
    public = bytes(private.public_key)
    return KeyPair(key_id_of(public), _b64(public), _b64(bytes(private)))


def seal(public_key: str, plaintext: bytes) -> bytes:
    """Encrypt for the holder of the private key. Needs the public key only."""
    raw = _decode_key(public_key, "public")
    key_id = key_id_of(raw).encode("ascii")
    return _MAGIC + bytes([len(key_id)]) + key_id + SealedBox(PublicKey(raw)).encrypt(plaintext)


def blob_key_id(blob: bytes) -> str:
    """The id of the key that sealed this blob (to find blobs due for re-encryption)."""
    if not blob.startswith(_MAGIC) or len(blob) < 5 or len(blob) < 4 + blob[3]:
        raise CryptoError("not a sealed credential")
    return blob[4:4 + blob[3]].decode("ascii", "replace")


def open_blob(blob: bytes, private_keys: Iterable[str]) -> bytes:
    """Decrypt with whichever of the private keys sealed the blob."""
    wanted = blob_key_id(blob)
    for text in private_keys:
        private = PrivateKey(_decode_key(text, "private"))
        if key_id_of(bytes(private.public_key)) == wanted:
            try:
                return SealedBox(private).decrypt(blob[4 + blob[3]:])
            except _NaClError:
                raise CryptoError("the sealed credential is corrupt") from None
    raise CryptoError("no private key for %s" % wanted)


def public_key_from_env(env: Mapping[str, str] = os.environ) -> str:
    key = env.get(PUBLIC_KEY_VAR, "").strip()
    if not key:
        raise CryptoError("%s is not set" % PUBLIC_KEY_VAR)
    return key


def private_keys_from_env(env: Mapping[str, str] = os.environ) -> list[str]:
    keys = [k.strip() for k in env.get(PRIVATE_KEY_VAR, "").split(",") if k.strip()]
    if not keys:
        raise CryptoError("%s is not set: this process cannot open credentials" % PRIVATE_KEY_VAR)
    return keys
