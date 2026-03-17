"""
delete_key.py  —  called by MANAGE_KEYS.bat
Usage: python delete_key.py KEY_NAME
"""
import sys
import os

if len(sys.argv) < 2:
    print("[ERROR] Usage: python delete_key.py KEY_NAME")
    sys.exit(1)

key_name = sys.argv[1].strip()

project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

try:
    from vault.vault import Vault
    v = Vault()
    ok = v.delete(key_name)
    if ok:
        print(f"[OK]   {key_name} deleted from vault.")
    else:
        print(f"[WARN] {key_name} not found in vault.")
except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)
