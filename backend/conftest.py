import sys
from pathlib import Path

# Make backend/ importable when running pytest from project root
sys.path.insert(0, str(Path(__file__).parent))
