"""``python3 -m server`` 入口——env 驱动（ZAI_PORT / AIASSISTANT_HOME）。"""
from server.app import main

if __name__ == "__main__":
    raise SystemExit(main())
