"""``python3 -m server`` 入口——env 驱动（ZAI_PORT / AIASSISTANT_HOME）。

契约：docs/CONTRACT.md §49（web 面 server）、§54.2（launchd 常驻托管）。
"""
from server.app import main

if __name__ == "__main__":
    raise SystemExit(main())
