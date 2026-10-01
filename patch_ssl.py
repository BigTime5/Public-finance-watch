"""
patch_ssl.py — Definitive SSL fix for Python 3.13 + urllib3 on Windows/Anaconda.

Run once:
    python patch_ssl.py

Then run as normal:
    python main.py --sources knbs cob treasury --export
"""

import sys
from pathlib import Path

HERE = Path(__file__).parent

# ── The patch block — prepended to config.py so it runs before EVERYTHING ────
SSL_PATCH = '''\
# ── SSL bypass (auto-added by patch_ssl.py) ─────────────────────────────────
# Patches BOTH Python ssl AND urllib3's internal context factory.
# Required on Windows/Anaconda Python 3.13 where urllib3 ignores verify=False.
import ssl as _ssl
import urllib3 as _urllib3
import urllib3.util.ssl_ as _urllib3_ssl_util

# Patch 1 — Python default HTTPS context
_ssl._create_default_https_context = _ssl._create_unverified_context

# Patch 2 — urllib3's OWN context factory (this is what requests actually uses)
_orig_create_urllib3_context = _urllib3_ssl_util.create_urllib3_context
def _no_verify_urllib3_context(*args, **kwargs):
    ctx = _orig_create_urllib3_context(*args, **kwargs)
    ctx.check_hostname = False
    ctx.verify_mode    = _ssl.CERT_NONE
    return ctx
_urllib3_ssl_util.create_urllib3_context = _no_verify_urllib3_context

# Patch 3 — silence InsecureRequestWarning
_urllib3.disable_warnings(_urllib3.exceptions.InsecureRequestWarning)
# ── End SSL bypass ────────────────────────────────────────────────────────────

'''

config_path = HERE / "config.py"
content     = config_path.read_text(encoding="utf-8")

if "End SSL bypass" in content:
    print("config.py already patched — skipping rewrite.")
else:
    config_path.write_text(SSL_PATCH + content, encoding="utf-8")
    print(f"  ✓  config.py patched ({config_path})")

# ── Test ──────────────────────────────────────────────────────────────────────
print("\nApplying patch in this process and testing knbs.or.ke ...")

import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import urllib3.util.ssl_ as _u
_orig = _u.create_urllib3_context
def _patched(*a, **k):
    c = _orig(*a, **k); c.check_hostname = False; c.verify_mode = ssl.CERT_NONE; return c
_u.create_urllib3_context = _patched

import urllib3
urllib3.disable_warnings()

import requests
try:
    r = requests.get("https://www.knbs.or.ke/", timeout=15)
    print(f"  ✓  knbs.or.ke → HTTP {r.status_code}  (SSL bypass WORKING)")
    print("\n  Run now:")
    print("    python main.py --sources knbs cob treasury --export")
except requests.exceptions.SSLError as e:
    print(f"  ✗  Still SSL error: {e}")
    print("\n  This means knbs.or.ke may be unreachable from your network.")
    print("  Try: python main.py --sources knbs --no-download")
    print("  (KNBS Excel files are already indexed; you can download them manually.)")
except Exception as e:
    print(f"  ✗  Network error (not SSL): {e}")
    print("  The SSL fix is applied — this is a connectivity issue, not SSL.")
