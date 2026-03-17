"""check_deps.py — checks if core packages are installed. Called by LAUNCH.bat."""
import sys
try:
    import websockets
    import anthropic
    import openai
    import cryptography
    sys.exit(0)   # all good
except ImportError:
    sys.exit(1)   # something missing
