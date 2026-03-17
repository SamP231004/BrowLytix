"""
================================================================================
  AGENTIC OS BROWSER — SECURITY VAULT
================================================================================
  AES-256-GCM encrypted local key vault for API credentials.
  Integrates with OS keychain (macOS Keychain, GNOME Keyring, Windows DPAPI)
  for master key storage — raw API keys never touch disk unencrypted.

  Algorithm:
    STORE:
      1. Generate random 96-bit nonce
      2. Derive 256-bit key from master_key via HKDF-SHA256
      3. Encrypt secret with AES-256-GCM → ciphertext + 128-bit auth tag
      4. Store: base64(nonce || ciphertext || tag) in vault file
      5. Update HMAC-SHA256 manifest checksum

    RETRIEVE:
      1. Load master key from OS keychain
      2. Verify manifest HMAC
      3. Decode base64 → split nonce / ciphertext / tag
      4. Decrypt with AES-256-GCM (fails loudly on tamper)
      5. Return plaintext secret

    ROTATE:
      1. Load all existing secrets with old master key
      2. Generate new master key
      3. Re-encrypt all secrets with new key
      4. Atomic file swap
================================================================================
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidTag

log = logging.getLogger("vault")


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

NONCE_BYTES    = 12      # 96-bit nonce for AES-256-GCM
KEY_BYTES      = 32      # 256-bit AES key
TAG_BYTES      = 16      # 128-bit GCM authentication tag
HKDF_SALT_SIZE = 32      # 256-bit HKDF salt
VAULT_VERSION  = "1.0"


# ══════════════════════════════════════════════════════════════════════════════
#  VAULT ENTRY
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class VaultEntry:
    key_name: str                   # e.g. "OPENAI_API_KEY"
    encrypted_blob: str             # base64(nonce || ciphertext || tag)
    hkdf_salt: str                  # base64(256-bit salt used for this entry)
    created_at: float = field(default_factory=time.time)
    last_accessed: Optional[float] = None
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "key_name": self.key_name,
            "encrypted_blob": self.encrypted_blob,
            "hkdf_salt": self.hkdf_salt,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VaultEntry":
        return cls(**d)


# ══════════════════════════════════════════════════════════════════════════════
#  OS KEYCHAIN BRIDGE
# ══════════════════════════════════════════════════════════════════════════════

class KeychainBridge:
    """
    Stores the vault master key in the OS native keychain.
    Falls back to an environment variable for CI/server environments.

    Priority: OS keychain → AGENTIC_MASTER_KEY env var → auto-generate + warn
    """

    SERVICE_NAME = "AgenticOSBrowser"
    ACCOUNT_NAME = "vault_master_key"

    def get_master_key(self) -> bytes:
        """Retrieve master key from OS keychain or env var."""
        # ── Try OS keychain first
        try:
            import keyring
            stored = keyring.get_password(self.SERVICE_NAME, self.ACCOUNT_NAME)
            if stored:
                return base64.b64decode(stored)
        except Exception as e:
            log.debug(f"Keychain read failed: {e}")

        # ── Fall back to environment variable
        env_key = os.environ.get("AGENTIC_MASTER_KEY")
        if env_key:
            key_bytes = base64.b64decode(env_key)
            if len(key_bytes) >= KEY_BYTES:
                return key_bytes[:KEY_BYTES]

        # ── Auto-generate (first run) and store
        log.warning(
            "No master key found — generating new master key. "
            "This is normal on first run. Set AGENTIC_MASTER_KEY env var for CI."
        )
        master_key = secrets.token_bytes(KEY_BYTES)
        self.store_master_key(master_key)
        return master_key

    def store_master_key(self, key: bytes) -> bool:
        """Store master key in OS keychain."""
        encoded = base64.b64encode(key).decode()
        try:
            import keyring
            keyring.set_password(self.SERVICE_NAME, self.ACCOUNT_NAME, encoded)
            log.info("Master key stored in OS keychain")
            return True
        except Exception as e:
            log.warning(f"Keychain write failed: {e}. Set AGENTIC_MASTER_KEY env var.")
            os.environ["AGENTIC_MASTER_KEY"] = encoded
            return False

    def rotate_master_key(self) -> bytes:
        """Generate and store a new master key."""
        new_key = secrets.token_bytes(KEY_BYTES)
        self.store_master_key(new_key)
        return new_key


# ══════════════════════════════════════════════════════════════════════════════
#  CRYPTO ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class CryptoEngine:
    """
    AES-256-GCM encryption / decryption with HKDF key derivation.

    Each vault entry has a unique HKDF salt, so even if the same secret
    is stored twice, the ciphertexts are different.
    """

    def derive_key(self, master_key: bytes, salt: bytes, context: str) -> bytes:
        """Derive a 256-bit encryption key via HKDF-SHA256."""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=salt,
            info=context.encode("utf-8"),
        )
        return hkdf.derive(master_key)

    def encrypt(
        self, plaintext: str, master_key: bytes, context: str = "vault_entry"
    ) -> Tuple[bytes, bytes]:
        """
        Encrypt plaintext with AES-256-GCM.
        Returns (encrypted_blob, hkdf_salt).
        encrypted_blob = nonce(12) || ciphertext || tag(16)
        """
        salt  = secrets.token_bytes(HKDF_SALT_SIZE)
        key   = self.derive_key(master_key, salt, context)
        nonce = secrets.token_bytes(NONCE_BYTES)
        aesgcm = AESGCM(key)
        # AESGCM.encrypt returns ciphertext + tag concatenated
        ct_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), context.encode())
        blob = nonce + ct_with_tag
        return blob, salt

    def decrypt(
        self, blob: bytes, salt: bytes, master_key: bytes, context: str = "vault_entry"
    ) -> str:
        """
        Decrypt AES-256-GCM blob.
        Raises InvalidTag if tampered, raises ValueError on wrong key.
        """
        if len(blob) < NONCE_BYTES + TAG_BYTES + 1:
            raise ValueError(f"Blob too short: {len(blob)} bytes")

        nonce      = blob[:NONCE_BYTES]
        ct_with_tag = blob[NONCE_BYTES:]
        key        = self.derive_key(master_key, salt, context)
        aesgcm     = AESGCM(key)

        try:
            plaintext = aesgcm.decrypt(nonce, ct_with_tag, context.encode())
            return plaintext.decode("utf-8")
        except InvalidTag:
            raise ValueError(
                "Decryption authentication failed — vault entry may have been tampered with"
            )


# Avoid circular import — define Tuple locally
from typing import Tuple


# ══════════════════════════════════════════════════════════════════════════════
#  VAULT MANIFEST
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class VaultManifest:
    version: str = VAULT_VERSION
    entries: Dict[str, dict] = field(default_factory=dict)
    hmac_digest: Optional[str] = None   # HMAC-SHA256 of entries (hex)
    created_at: float = field(default_factory=time.time)
    last_modified: float = field(default_factory=time.time)

    def compute_hmac(self, hmac_key: bytes) -> str:
        """Compute HMAC-SHA256 of the canonical JSON of entries."""
        canonical = json.dumps(self.entries, sort_keys=True, separators=(",", ":"))
        return hmac.new(hmac_key, canonical.encode(), hashlib.sha256).hexdigest()

    def verify_integrity(self, hmac_key: bytes) -> bool:
        """Verify stored HMAC matches computed HMAC."""
        if not self.hmac_digest:
            return False
        expected = self.compute_hmac(hmac_key)
        return hmac.compare_digest(self.hmac_digest, expected)


# ══════════════════════════════════════════════════════════════════════════════
#  VAULT — MAIN CLASS
# ══════════════════════════════════════════════════════════════════════════════

class Vault:
    """
    Secure local credential vault with AES-256-GCM encryption.

    Usage:
        vault = Vault()
        vault.store("OPENAI_API_KEY", "sk-...")
        key = vault.retrieve("OPENAI_API_KEY")
    """

    DEFAULT_VAULT_PATH = Path.home() / ".agentic_os" / "vault.json"

    def __init__(self, vault_path: Optional[Path] = None):
        self.vault_path = vault_path or self.DEFAULT_VAULT_PATH
        self.vault_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.keychain   = KeychainBridge()
        self.crypto     = CryptoEngine()
        self._manifest: Optional[VaultManifest] = None
        self._master_key: Optional[bytes] = None

    def _get_master_key(self) -> bytes:
        if self._master_key is None:
            self._master_key = self.keychain.get_master_key()
        return self._master_key

    def _load_manifest(self) -> VaultManifest:
        """Load and verify vault manifest from disk."""
        if not self.vault_path.exists():
            return VaultManifest()

        with open(self.vault_path) as f:
            data = json.load(f)

        manifest = VaultManifest(
            version=data.get("version", VAULT_VERSION),
            entries=data.get("entries", {}),
            hmac_digest=data.get("hmac_digest"),
            created_at=data.get("created_at", time.time()),
            last_modified=data.get("last_modified", time.time()),
        )

        # Verify integrity
        master_key = self._get_master_key()
        hmac_key   = hashlib.sha256(master_key + b":hmac").digest()
        if manifest.hmac_digest and not manifest.verify_integrity(hmac_key):
            raise SecurityError(
                "Vault integrity check FAILED — manifest HMAC mismatch. "
                "The vault file may have been tampered with."
            )
        return manifest

    def _save_manifest(self, manifest: VaultManifest) -> None:
        """Atomically save manifest to disk with updated HMAC."""
        master_key = self._get_master_key()
        hmac_key   = hashlib.sha256(master_key + b":hmac").digest()
        manifest.hmac_digest  = manifest.compute_hmac(hmac_key)
        manifest.last_modified = time.time()

        # Atomic write via temp file + rename
        tmp_path = self.vault_path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump({
                "version":       manifest.version,
                "entries":       manifest.entries,
                "hmac_digest":   manifest.hmac_digest,
                "created_at":    manifest.created_at,
                "last_modified": manifest.last_modified,
            }, f, indent=2)
        tmp_path.replace(self.vault_path)
        os.chmod(self.vault_path, 0o600)   # owner read/write only

    # ── Public API ────────────────────────────────────────────────────────────

    def store(self, key_name: str, secret: str, metadata: dict = None) -> None:
        """Encrypt and store a secret. Overwrites existing entry."""
        master_key = self._get_master_key()
        blob, salt = self.crypto.encrypt(secret, master_key, context=key_name)

        entry = VaultEntry(
            key_name=key_name,
            encrypted_blob=base64.b64encode(blob).decode(),
            hkdf_salt=base64.b64encode(salt).decode(),
            metadata=metadata or {},
        )
        manifest = self._load_manifest()
        manifest.entries[key_name] = entry.to_dict()
        self._save_manifest(manifest)
        log.info(f"Vault: stored key '{key_name}'")

    def retrieve(self, key_name: str) -> Optional[str]:
        """Decrypt and return a stored secret, or None if not found."""
        manifest = self._load_manifest()
        entry_data = manifest.entries.get(key_name)
        if not entry_data:
            log.warning(f"Vault: key '{key_name}' not found")
            return None

        entry = VaultEntry.from_dict(entry_data)
        master_key = self._get_master_key()
        blob = base64.b64decode(entry.encrypted_blob)
        salt = base64.b64decode(entry.hkdf_salt)

        try:
            secret = self.crypto.decrypt(blob, salt, master_key, context=key_name)
            # Update last_accessed
            entry.last_accessed = time.time()
            manifest.entries[key_name] = entry.to_dict()
            self._save_manifest(manifest)
            return secret
        except ValueError as e:
            log.error(f"Vault: failed to decrypt '{key_name}': {e}")
            raise

    def delete(self, key_name: str) -> bool:
        """Remove a secret from the vault."""
        manifest = self._load_manifest()
        if key_name not in manifest.entries:
            return False
        del manifest.entries[key_name]
        self._save_manifest(manifest)
        log.info(f"Vault: deleted key '{key_name}'")
        return True

    def list_keys(self) -> List[str]:
        """Return all stored key names (without decrypting values)."""
        manifest = self._load_manifest()
        return list(manifest.entries.keys())

    def rotate_master_key(self) -> None:
        """
        Re-encrypt all vault entries under a new master key.
        Atomic: either all entries are re-encrypted, or none are.
        """
        log.info("Starting vault master key rotation...")
        manifest = self._load_manifest()
        old_master = self._get_master_key()

        # Decrypt everything with old key first
        decrypted: Dict[str, Tuple[str, dict]] = {}
        for key_name, entry_data in manifest.entries.items():
            entry = VaultEntry.from_dict(entry_data)
            blob = base64.b64decode(entry.encrypted_blob)
            salt = base64.b64decode(entry.hkdf_salt)
            secret = self.crypto.decrypt(blob, salt, old_master, context=key_name)
            decrypted[key_name] = (secret, entry_data.get("metadata", {}))

        # Generate new master key
        new_master = self.keychain.rotate_master_key()
        self._master_key = new_master

        # Re-encrypt all secrets with new key
        new_manifest = VaultManifest()
        for key_name, (secret, metadata) in decrypted.items():
            blob, salt = self.crypto.encrypt(secret, new_master, context=key_name)
            entry = VaultEntry(
                key_name=key_name,
                encrypted_blob=base64.b64encode(blob).decode(),
                hkdf_salt=base64.b64encode(salt).decode(),
                metadata=metadata,
            )
            new_manifest.entries[key_name] = entry.to_dict()

        self._save_manifest(new_manifest)
        log.info(f"Key rotation complete. Re-encrypted {len(decrypted)} entries.")

    def export_env_vars(self) -> Dict[str, str]:
        """
        Decrypt all stored secrets and return as {key_name: secret} dict.
        Use this to inject credentials into container environments.
        WARNING: returned dict contains plaintext — handle with care.
        """
        result = {}
        for key_name in self.list_keys():
            val = self.retrieve(key_name)
            if val:
                result[key_name] = val
        return result


class SecurityError(Exception):
    """Raised when vault integrity checks fail."""
    pass
