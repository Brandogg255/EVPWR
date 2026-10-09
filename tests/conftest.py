import sys
from pathlib import Path

COMPONENTS = Path(__file__).resolve().parents[1] / "custom_components" / "evpwr"
sys.path.insert(0, str(COMPONENTS))
