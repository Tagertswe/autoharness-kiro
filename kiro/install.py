"""Run the autoharness installer for Kiro from a checkout, without setting PYTHONPATH by hand.

    python3 kiro/install.py [--scope global|workspace] [--into AGENT] [--env KEY=VALUE] [--uninstall]

See src/autoharness/install.py and docs/kiro.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoharness import install  # noqa: E402

if __name__ == "__main__":
    sys.exit(install.main())
