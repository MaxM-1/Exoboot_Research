import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-pytest")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
