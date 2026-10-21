# tests/conftest.py
import sys
from pathlib import Path

# Ensure EAgent framework root is first in sys.path so that
# `from helpers import ...` resolves to EAgent's helpers/, not
# the Commands plugin's local helpers/ directory.
ea_root = str(Path(__file__).resolve().parents[3])  # tests/ → _commands → plugins → repo root
while ea_root in sys.path:
    sys.path.remove(ea_root)
sys.path.insert(0, ea_root)
