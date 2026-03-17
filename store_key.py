"""
store_key.py  —  called by SETUP_FIRST_TIME.bat and MANAGE_KEYS.bat
Usage: python store_key.py <KEY_NAME> <KEY_VALUE>
"""
import sys
import os

if len(sys.argv) < 3:
    print("[ERROR] Usage: python store_key.py KEY_NAME KEY_VALUE")
    sys.exit(1)

key_name  = sys.argv[1].strip()
key_value = sys.argv[2].strip()

if not key_value:
    print("[SKIP] Empty value — key not stored.")
    sys.exit(0)

# Add project root to path
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

# Try vault first
try:
    from vault.vault import Vault
    v = Vault()
    v.store(key_name, key_value)
    print(f"[OK]   {key_name} stored securely in encrypted vault.")
    sys.exit(0)
except Exception as e:
    print(f"[WARN] Vault storage failed: {e}")
    print("[INFO] Falling back to Windows environment variable...")

# Fallback: save as a persistent Windows environment variable
try:
    import subprocess
    result = subprocess.run(
        ["setx", key_name, key_value],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"[OK]   {key_name} saved as environment variable.")
        print(f"[INFO] Restart your terminal for the variable to take effect.")
    else:
        print(f"[ERROR] setx failed: {result.stderr}")
        sys.exit(1)
except Exception as e:
    print(f"[ERROR] Could not save key: {e}")
    sys.exit(1)
