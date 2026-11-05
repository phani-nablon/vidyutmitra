"""One minimal call to the configured model. Run from the substation_retrieval folder:

    set PYTHONPATH=src
    python test_llm.py

Expected output: {'ok': True}
"""
import sys

from substation_retrieval.config import load_settings
from substation_retrieval.extraction.llm import LLMClient

settings = load_settings()
client = LLMClient(settings)
print("provider:", client.provider, "| model:", client.model, "| style:", client.style)
print("key present:", client.available)

try:
    reply = client.complete("Reply with JSON only.", 'Return a JSON object: {"ok": true}.', None, json_mode=True)
    print("reply:", reply)
except Exception as exc:  # show the failure type so the cause is clear
    print("FAILED:", type(exc).__name__, "-", str(exc)[:300])
    sys.exit(1)