"""launchd entrypoint for the self-updating deploy job (CONTRACT §56).

``python3 -m act.auto_deploy`` runs ``scripts/auto-deploy.sh`` — the actual
logic (fetch, ff-only merge, install.sh --non-interactive, doctor-gated
rollback, state/deploy_state.json) lives in that shell script so it can be
tested against a throwaway git repo (tests/integration).

Why a python shim instead of ``/bin/bash <script>`` in the plist: §55 pins
``ProgramArguments[0]`` of every agent to the pinned, launchd-viable
interpreter — the one binary proven to read the repo when launchd (not a
terminal) spawns it (TCC is granted per binary). The bash child inherits this
process as its TCC responsible process, and the doctor's ``launchd python``
probe (``argv0 -c 'import yaml'``) keeps working for this agent too. The
interpreter is handed down as ``AIASSISTANT_PYTHON`` so the script's own
doctor/notify calls use the very same one.

Run by hand:  python3 -m act.auto_deploy [--force]
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Optional

from act.lib import config

SCRIPT = config.HOME / "scripts" / "auto-deploy.sh"


def main(argv: Optional[list] = None,
         run: Callable[..., int] = subprocess.call) -> int:
    """Exec the deploy script under this interpreter; its exit code is ours.

    ``run`` is the injection seam for tests (never spawns a real script there).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not SCRIPT.is_file():
        print("auto_deploy: %s missing — incomplete checkout?" % SCRIPT,
              file=sys.stderr)
        return 1
    env = dict(os.environ)
    env["AIASSISTANT_PYTHON"] = sys.executable
    env["AIASSISTANT_HOME"] = str(config.HOME)
    return int(run(["/bin/bash", str(SCRIPT), *args], env=env))


if __name__ == "__main__":
    raise SystemExit(main())
