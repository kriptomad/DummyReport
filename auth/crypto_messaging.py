"""
auth/crypto_messaging.py
=========================
Hybrid RSA + AES-GCM encryption for the internal messaging system, so that
message BODIES are unreadable to anyone inspecting data/messages.json on
disk — including whoever hosts/runs the app — without the recipient's own
login password.

How it works
------------
- Every user gets an RSA-2048 keypair generated at registration time.
- The PUBLIC key is stored in the clear in data/users.json — that's safe
  and expected for asymmetric crypto (anyone can encrypt TO a user, only
  that user can decrypt).
- The PRIVATE key is encrypted with a Fernet key derived (PBKDF2-HMAC-SHA256,
  200k iterations) from the user's own password, using a salt that is
  DIFFERENT from the one used for their login-password hash — so the
  private-key-encryption key can never be reconstructed from the stored
  auth hash alone.
- The private key is only ever decrypted in memory, at login time (while
  the app momentarily has the user's plaintext password on the way to
  verifying it), and kept in st.session_state for that browser session —
  it is NEVER written back to disk in decrypted form.
- To send a message: encrypt a random AES-256 key with the RECIPIENT's
  public key (RSA-OAEP), then encrypt the message body with that AES key
  (AES-256-GCM). Both ciphertexts (and the nonce) are stored as base64.
- To read a message: the recipient's own in-memory, password-unlocked
  private key unwraps the AES key, then decrypts the body.

Disclosed limitation
---------------------
Sender/recipient CWS are stored in the clear in messages.json because the
app needs that information to route messages to the correct inbox and
display "from"/"to" — only the message BODY is end-to-end encrypted this
way. Fully hiding *who* messaged *whom* would require an anonymous-routing
infrastructure that's out of scope for a self-hosted internal tool.

Password-reset key escrow
--------------------------
Because the private key is normally only unwrappable with the user's OWN
password, an admin-triggered reset (where the old password is unknown)
used to force generation of a BRAND NEW keypair — silently and permanently
destroying the ability to read every message the user had received before
the reset. To fix that data-loss risk, every keypair is now ALSO wrapped
with a local server-side "escrow" key (data/.msg_escrow.key, generated
once on first use, never displayed/exported) and stored as
`escrowed_private_key`. An admin reset now unwraps the private key via
escrow instead of discarding it, so the SAME keypair (and therefore every
previously-sent/received message) stays readable after the reset.
"""
import base64
import os
from typing import Dict, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 200_000

_ESCROW_KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", ".msg_escrow.key")


def _derive_fernet_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive((password or "").encode("utf-8")))


def _get_or_create_escrow_key() -> bytes:
    """
    Loads (or generates, on first use) a local Fernet key used ONLY to let
    an admin-triggered password reset recover a user's existing messaging
    keypair without knowing their old password. Lives in data/ next to the
    other at-rest app state (users.json, messages.json) — never logged,
    never shown in any UI, never e-mailed.
    """
    os.makedirs(os.path.dirname(_ESCROW_KEY_PATH), exist_ok=True)
    if os.path.exists(_ESCROW_KEY_PATH):
        with open(_ESCROW_KEY_PATH, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    with open(_ESCROW_KEY_PATH, "wb") as f:
        f.write(key)
    try:
        os.chmod(_ESCROW_KEY_PATH, 0o600)
    except OSError:
        pass  # best-effort on platforms without POSIX permission bits (e.g. Windows)
    return key


def _escrow_wrap(private_pem: bytes) -> str:
    return base64.b64encode(Fernet(_get_or_create_escrow_key()).encrypt(private_pem)).decode("utf-8")


def escrow_wrap_existing_private_key(private_pem: str) -> str:
    """Public wrapper of _escrow_wrap for backfilling escrow on pre-existing keypairs (str PEM in, b64 out)."""
    return _escrow_wrap(private_pem.encode("utf-8"))


def escrow_unwrap_private_key(escrowed_private_key_b64: str) -> Optional[str]:
    """Recovers the PEM private key from its escrow copy (used by admin_reset_password)."""
    if not escrowed_private_key_b64:
        return None
    try:
        private_pem = Fernet(_get_or_create_escrow_key()).decrypt(base64.b64decode(escrowed_private_key_b64))
        return private_pem.decode("utf-8")
    except (InvalidToken, ValueError, Exception):
        return None


def rewrap_private_key_with_new_password(private_pem: str, new_password: str) -> Tuple[str, str]:
    """Wraps an already-known-plaintext private key (e.g. recovered via escrow) with a new password."""
    new_salt = os.urandom(16)
    fernet_key = _derive_fernet_key(new_password, new_salt)
    encrypted_private = Fernet(fernet_key).encrypt(private_pem.encode("utf-8"))
    return base64.b64encode(encrypted_private).decode("utf-8"), new_salt.hex()


def generate_keypair_for_user(password: str) -> Tuple[str, str, str, str]:
    """
    Generates a fresh RSA-2048 keypair for a new user.
    Returns (public_key_pem, encrypted_private_key_b64, salt_hex, escrowed_private_key_b64).
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    salt = os.urandom(16)
    fernet_key = _derive_fernet_key(password, salt)
    encrypted_private = Fernet(fernet_key).encrypt(private_pem)

    return (
        public_pem.decode("utf-8"),
        base64.b64encode(encrypted_private).decode("utf-8"),
        salt.hex(),
        _escrow_wrap(private_pem),
    )


def decrypt_private_key(password: str, encrypted_private_key_b64: str, salt_hex: str) -> Optional[str]:
    """Returns the PEM-encoded private key as a string, or None if the password is wrong."""
    if not encrypted_private_key_b64 or not salt_hex:
        return None
    try:
        salt = bytes.fromhex(salt_hex)
        fernet_key = _derive_fernet_key(password, salt)
        private_pem = Fernet(fernet_key).decrypt(base64.b64decode(encrypted_private_key_b64))
        return private_pem.decode("utf-8")
    except (InvalidToken, ValueError, Exception):
        return None


def reencrypt_private_key(
    old_password: str, new_password: str, encrypted_private_key_b64: str, salt_hex: str
) -> Optional[Tuple[str, str]]:
    """
    Used when a user changes their password — unwraps the private key with
    the OLD password-derived key and re-wraps it with the NEW one, so
    encrypted messages remain readable after a password change. Returns
    (new_encrypted_private_key_b64, new_salt_hex), or None if old_password
    was wrong / no keypair existed yet.
    """
    private_pem = decrypt_private_key(old_password, encrypted_private_key_b64, salt_hex)
    if private_pem is None:
        return None
    new_salt = os.urandom(16)
    fernet_key = _derive_fernet_key(new_password, new_salt)
    encrypted_private = Fernet(fernet_key).encrypt(private_pem.encode("utf-8"))
    return base64.b64encode(encrypted_private).decode("utf-8"), new_salt.hex()


def encrypt_message(body: str, recipient_public_key_pem: str) -> Dict[str, str]:
    """Hybrid RSA+AES encryption. Returns a dict of base64 strings safe for JSON storage."""
    public_key = serialization.load_pem_public_key(recipient_public_key_pem.encode("utf-8"))

    aes_key = os.urandom(32)
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(nonce, (body or "").encode("utf-8"), None)

    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )

    return {
        "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
        "nonce": base64.b64encode(nonce).decode("utf-8"),
        "encrypted_key": base64.b64encode(encrypted_key).decode("utf-8"),
    }


def decrypt_message(encrypted: Dict[str, str], private_key_pem: str) -> Optional[str]:
    """Returns the plaintext body, or None if decryption fails (wrong/missing key)."""
    if not private_key_pem or not encrypted:
        return None
    try:
        private_key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        aes_key = private_key.decrypt(
            base64.b64decode(encrypted["encrypted_key"]),
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        nonce = base64.b64decode(encrypted["nonce"])
        ciphertext = base64.b64decode(encrypted["ciphertext"])
        plaintext = AESGCM(aes_key).decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")
    except Exception:
        return None
