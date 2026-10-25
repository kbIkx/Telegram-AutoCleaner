"""Windows pythonw entry point; application logs still rotate in logs/."""

import os
import sys

from run import run

if __name__ == "__main__":
    with open(os.devnull, "w", encoding="utf-8") as sink:
        sys.stdout = sink
        sys.stderr = sink
        raise SystemExit(run())
