"""The version string is how pip tells two toolkit builds apart -- keep the two copies in step."""
import tomllib
from pathlib import Path

import binance_trading_toolkit


def test_package_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert binance_trading_toolkit.__version__ == pyproject["project"]["version"]
