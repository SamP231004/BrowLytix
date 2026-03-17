"""
list_keys.py  —  called by MANAGE_KEYS.bat
Usage: python list_keys.py
"""
import sys
import os

project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

try:
    from vault.vault import Vault
    v = Vault()
    keys = v.list_keys()
    if keys:
        print("[OK]   Keys stored in vault:")
        for k in keys:
            print(f"         - {k}")
    else:
        print("[INFO] No keys stored in vault yet.")
except Exception as e:
    print(f"[WARN] Could not read vault: {e}")
