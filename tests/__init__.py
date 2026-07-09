"""Test bootstrap: point AIASSISTANT_HOME at a throwaway tmp dir BEFORE any
``act.*`` import, so module-level path constants (config.HOME, STATE_DIR,
REGISTRY_DIR, secrets.SECRETS_DIR, analytics dirs, ...) all resolve inside the
sandbox and no test ever touches the real repo/state or Zelin's real keys.

Run the suite from the repo root:
    python3 -m unittest discover -s tests -v
"""
import os
import tempfile

TMP_HOME = tempfile.mkdtemp(prefix="aiassistant-test-home-")
os.environ["AIASSISTANT_HOME"] = TMP_HOME
