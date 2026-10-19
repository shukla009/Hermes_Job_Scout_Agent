"""Authenticated encryption for GitHub state, readable during rolling upgrades.

The existing bot secret is available to both Actions and the Worker. A separately
derived key keeps private application state out of the public repository. Changing
that secret requires decrypting/migrating with the old secret first.
"""
import base64
import hashlib
import json
import os
from pathlib import Path

FORMAT = "hermes.aesgcm.v1"


def state_key():
    secret = os.getenv("SCOUT_STATE_KEY") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not secret:
        raise ValueError("State decryption key is unavailable")
    return hashlib.sha256(b"hermes-private-state-v1\0" + secret.encode()).digest()


def decode_state(value, context=""):
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        return value
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = base64.b64decode(value["nonce"], validate=True)
    ciphertext = base64.b64decode(value["ciphertext"], validate=True)
    if len(nonce) != 12 or value.get("context") != context:
        raise ValueError("Invalid encrypted state context")
    try:
        return json.loads(AESGCM(state_key()).decrypt(nonce, ciphertext, context.encode()))
    except Exception:
        raise ValueError("Encrypted state authentication failed") from None


def encode_state(value, context=""):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    plaintext = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    ciphertext = AESGCM(state_key()).encrypt(nonce, plaintext, context.encode())
    return {"format": FORMAT, "context": context,
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode()}


def load_json(path):
    path = Path(path)
    return decode_state(json.loads(path.read_text(encoding="utf-8")), path.as_posix())
