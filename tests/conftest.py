"""Root conftest — adds src/ to sys.path so vesselx/spyhop packages import cleanly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
