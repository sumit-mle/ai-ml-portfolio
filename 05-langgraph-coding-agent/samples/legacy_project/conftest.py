"""Make billing.py importable when pytest runs from the legacy_project root."""
import sys
from pathlib import Path

# Ensure the legacy_project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))
