"""Source-tree launcher; contains no machine-specific paths or credentials."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from line_bridge.cli import main

if __name__ == "__main__":
    main()
