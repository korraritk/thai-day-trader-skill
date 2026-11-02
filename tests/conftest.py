import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("skills/th-trader-scanner", "skills/th-trader-exit", "skills/th-trader-timemachine",
            "skills/th-trader-budget", "skills/th-trader-risk", "skills/th-trader-entry",
            "skills/th-trader-alerts", "skills/th-trader-news", "shared"):
    sys.path.insert(0, os.path.join(ROOT, sub))

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
