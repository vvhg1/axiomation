"""A person's keys and the record's cipher (`docs/architecture.md` § 4).

A person holds a reading key (X25519), made on their machine by
`axiomation keys` with a recovery key beside it, whose secret is shown
once to be kept offline and whose public half stays. A paper's record
is sealed under a paper key, one per epoch, which a grant entry wraps
to each member's reading key and to the recovery key, as `age`'s
recipient stanza does: X25519, HKDF and ChaCha20-Poly1305. An entry's
body is compressed, then sealed under its epoch's paper key with
ChaCha20-Poly1305, its clear header as associated data.

A person's word is signed with OpenSSH (`ssh-keygen -Y`) under a
signing key of their own, made beside the reading key and locked by a
passphrase `ssh-keygen` asks for itself: an agent's shell holds the
key's file, never its passphrase.

The standard library and `cryptography` only, with `ssh-keygen` run as
a program: the records read, seal and sign through this module."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: The variable naming the directory a person's keys are in, in place of
#: the user's configuration directory.
ENV = "AXIOMATION_KEYS"
#: The reading key's secret, in the keys' directory.
READING = "reading.key"
#: The recovery key's public half, in the keys' directory; its secret
#: is shown once and never kept here.
RECOVERY = "recovery.pub"
#: The signing key, an OpenSSH key, in the keys' directory; its public
#: half beside it as `signing.pub`.
SIGNING = "signing"
#: The namespace a person's word is signed in (`ssh-keygen -Y`).
NAMESPACE = "axiomation-person"
#: How a member's card begins: the line a person gives the author to be
#: made a member (`card`).
CARD = "axiomation-member"
#: How a secret key is written as one line (the recovery key on paper).
SECRET_PREFIX = "AXIOMATION-SECRET-KEY-"
_WRAP_INFO = b"axiomation paper key"
_NONCE = 12


class KeysError(Exception):
    """A person's keys missing, unreadable, or not ones a record grants."""


def directory() -> Path:
    """Where this person's keys are: `AXIOMATION_KEYS` where it is set,
    else `axiomation/keys` in the user's configuration directory."""
    named = os.environ.get(ENV)
    if named:
        return Path(named)
    # the person's axiomation directory, as `user_settings.directory` has it
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "axiomation" / "keys"


def secret_text(key: X25519PrivateKey) -> str:
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    return SECRET_PREFIX + base64.b32encode(raw).decode("ascii").rstrip("=")


def secret_from(text: str) -> X25519PrivateKey:
    raw = text.strip()
    if not raw.startswith(SECRET_PREFIX):
        raise KeysError(f"not a secret key: it does not begin {SECRET_PREFIX}")
    raw = raw.removeprefix(SECRET_PREFIX)
    try:
        return X25519PrivateKey.from_private_bytes(
            base64.b32decode(raw + "=" * (-len(raw) % 8))
        )
    except ValueError as error:
        raise KeysError(f"not a secret key: {error}") from None


def public_text(key: X25519PublicKey) -> str:
    return base64.b64encode(key.public_bytes_raw()).decode("ascii")


def public_from(text: str) -> X25519PublicKey:
    try:
        return X25519PublicKey.from_public_bytes(base64.b64decode(text.strip()))
    except ValueError as error:
        raise KeysError(f"not a public key: {error}") from None


def key_id(key: X25519PublicKey) -> str:
    """A public key's short name, as a grant's stanza and an entry's
    writer name it."""
    return hashlib.sha256(key.public_bytes_raw()).hexdigest()[:16]


@dataclass(frozen=True)
class Member:
    """A member of a record as its grants name them: the public half of
    their reading key, which each epoch's paper key is wrapped to, and of
    their signing key, which their confirmations are checked against."""

    reading: X25519PublicKey
    signer: str | None

    @property
    def id(self) -> str:
        return key_id(self.reading)


def card(member: Member) -> str:
    """A member as one line, to be given to the author who adds them."""
    return f"{CARD} {public_text(member.reading)} {member.signer or '-'}"


def card_from(text: str) -> Member:
    words = text.split()
    if len(words) not in (3, 4) or words[0] != CARD:
        raise KeysError(
            f"not a member's card: it is `{CARD} READING SIGNER`, as "
            "`axiomation keys --card` prints it"
        )
    signer = None if words[2:] == ["-"] else " ".join(words[2:])
    return Member(public_from(words[1]), signer)


@dataclass(frozen=True)
class Keys:
    """A person's keys as a record uses them: the reading key, the
    recovery key's public half, to which every new paper key is wrapped
    as well, and the signing key: its file, and its public half as one
    line (its type and key, as an allowed-signers file names it); None
    where the person has none yet, who reads but signs nothing. `home`:
    the directory they were read from, where the tips this machine held
    of each record are kept beside them; None for keys held in memory
    alone, which keep none."""

    reading: X25519PrivateKey
    recovery: X25519PublicKey
    signing: Path | None = None
    signer: str | None = None
    home: Path | None = None

    @property
    def member(self) -> Member:
        return Member(self.reading.public_key(), self.signer)

    def signing_key(self) -> tuple[Path, str]:
        """The signing key's file and public half; refused where there is
        none."""
        if self.signing is None or self.signer is None:
            raise KeysError("no signing key: make it with `axiomation keys --make`")
        return self.signing, self.signer

    @property
    def id(self) -> str:
        return key_id(self.reading.public_key())


def made(at: Path | None = None, locked: bool = False) -> tuple[Keys, str | None]:
    """A new reading key, recovery key and signing key, written in `at`
    (the keys' directory where none is named); with the recovery key's
    secret as one line, which is not kept. Where `locked`, `ssh-keygen`
    asks for the signing key's passphrase at the terminal itself, so it
    is never on a command line another process can read; else the key
    has none. Where a reading key is there already and no signing key,
    the signing key alone is made, and no recovery secret given; where a
    signing key is there, refused: a key in use is never replaced."""
    at = directory() if at is None else at
    if (at / SIGNING).exists():
        raise KeysError(f"{at} holds your keys already")
    at.mkdir(parents=True, exist_ok=True)
    recovered = None
    if not (at / READING).exists():
        reading, recovery = X25519PrivateKey.generate(), X25519PrivateKey.generate()
        path = at / READING
        path.touch(mode=0o600)
        path.write_text(secret_text(reading) + "\n", encoding="utf-8")
        (at / RECOVERY).write_text(
            public_text(recovery.public_key()) + "\n", encoding="utf-8"
        )
        recovered = secret_text(recovery)
    _ssh_keygen(
        "-q",
        "-t",
        "ed25519",
        *(() if locked else ("-N", "")),
        "-C",
        "axiomation",
        "-f",
        str(at / SIGNING),
    )
    return mine(at), recovered


def is_locked(mine_: Keys) -> bool:
    """Whether the signing key asks for a passphrase."""
    return (
        _ssh_keygen(
            "-y", "-P", "", "-f", str(mine_.signing_key()[0]), check=False
        ).returncode
        != 0
    )


def mine(at: Path | None = None) -> Keys:
    """This person's keys, from `at` (the keys' directory where none is
    named)."""
    at = directory() if at is None else at
    try:
        reading = (at / READING).read_text(encoding="utf-8")
        recovery = (at / RECOVERY).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise KeysError(
            f"no keys in {at}: make them with `axiomation keys --make` "
            f"(or name their directory in {ENV})"
        ) from None
    found = Keys(secret_from(reading), public_from(recovery), home=at)
    public = at / f"{SIGNING}.pub"
    if not public.is_file():
        return found
    signer = " ".join(public.read_text(encoding="utf-8").split()[:2])
    return Keys(found.reading, found.recovery, at / SIGNING, signer, at)


def _ssh_keygen(
    *arguments: str, data: bytes | None = None, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    """`ssh-keygen` run with `arguments`; its prompts, a passphrase's,
    go to the terminal, never through this process."""
    program = shutil.which("ssh-keygen")
    if program is None:
        raise KeysError(
            "ssh-keygen is not on PATH: install OpenSSH's client, which signs "
            "a person's word"
        )
    done = subprocess.run(
        [program, *arguments],
        input=data,
        capture_output=True,
        check=False,
    )
    if check and done.returncode != 0:
        said = done.stderr.decode("utf-8", "replace").strip()
        raise KeysError(f"ssh-keygen failed: {said or done.returncode}")
    return done


def signed(mine_: Keys, data: bytes) -> str:
    """`data` signed with the person's signing key in the person's
    namespace; `ssh-keygen` asks for its passphrase where it has one."""
    with tempfile.TemporaryDirectory(prefix="axiomation-sign-") as scratch:
        message = Path(scratch) / "message"
        message.write_bytes(data)
        _ssh_keygen(
            "-q",
            "-Y",
            "sign",
            "-f",
            str(mine_.signing_key()[0]),
            "-n",
            NAMESPACE,
            str(message),
        )
        return (Path(scratch) / "message.sig").read_text(encoding="ascii")


def verified(signer: str, principal: str, data: bytes, signature: str) -> bool:
    """Whether `signature` is `principal`'s, by the signing key `signer`,
    on `data` in the person's namespace."""
    with tempfile.TemporaryDirectory(prefix="axiomation-verify-") as scratch:
        allowed = Path(scratch) / "allowed"
        allowed.write_text(
            f'{principal} namespaces="{NAMESPACE}" {signer}\n', encoding="utf-8"
        )
        sig = Path(scratch) / "sig"
        sig.write_text(signature, encoding="ascii")
        done = _ssh_keygen(
            "-Y",
            "verify",
            "-f",
            str(allowed),
            "-I",
            principal,
            "-n",
            NAMESPACE,
            "-s",
            str(sig),
            data=data,
            check=False,
        )
        return done.returncode == 0


def paper_key() -> bytes:
    return ChaCha20Poly1305.generate_key()


def _wrapping(shared: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    return HKDF(SHA256(), 32, ephemeral + recipient, _WRAP_INFO).derive(shared)


def wrapped(secret: bytes, to: X25519PublicKey) -> dict[str, str]:
    """`secret` wrapped to one reading key: a grant's stanza."""
    ephemeral = X25519PrivateKey.generate()
    mine_ = ephemeral.public_key().public_bytes_raw()
    key = _wrapping(ephemeral.exchange(to), mine_, to.public_bytes_raw())
    sealed = ChaCha20Poly1305(key).encrypt(bytes(_NONCE), secret, None)
    return {
        "to": key_id(to),
        "ephemeral": base64.b64encode(mine_).decode("ascii"),
        "key": base64.b64encode(sealed).decode("ascii"),
    }


def unwrapped(stanza: dict[str, str], reading: X25519PrivateKey) -> bytes | None:
    """The secret a stanza wraps, where it is wrapped to `reading`; None
    where it is not."""
    if stanza.get("to") != key_id(reading.public_key()):
        return None
    try:
        theirs = base64.b64decode(stanza["ephemeral"])
        key = _wrapping(
            reading.exchange(X25519PublicKey.from_public_bytes(theirs)),
            theirs,
            reading.public_key().public_bytes_raw(),
        )
        return ChaCha20Poly1305(key).decrypt(
            bytes(_NONCE), base64.b64decode(stanza["key"]), None
        )
    except (InvalidTag, KeyError, ValueError):
        return None


def nonce() -> bytes:
    return os.urandom(_NONCE)


def salt() -> str:
    return base64.b64encode(os.urandom(16)).decode("ascii")


def sealed(key: bytes, nonce: bytes, plain: bytes, header: bytes) -> bytes:
    return ChaCha20Poly1305(key).encrypt(nonce, plain, header)


def opened(key: bytes, nonce: bytes, data: bytes, header: bytes) -> bytes | None:
    """`data` opened under `key`; None where it was not sealed under it
    with this header."""
    try:
        return ChaCha20Poly1305(key).decrypt(nonce, data, header)
    except InvalidTag:
        return None
