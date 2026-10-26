"""Keep externally provisioned live tests out of ordinary collection."""

from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Enable tests/live; requires explicit disposable service configuration",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--live"):
        return
    live_root = Path(__file__).parent / "packages/decepticon/tests/live"
    for argument in config.args:
        selected = Path(argument.split("::", 1)[0]).resolve()
        if selected == live_root or live_root in selected.parents:
            raise pytest.UsageError("Selecting tests/live requires explicit --live")


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    live_root = Path(__file__).parent / "packages/decepticon/tests/live"
    if (
        collection_path == live_root or live_root in collection_path.parents
    ) and not config.getoption("--live"):
        return True
    return None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    root = Path(__file__).parent / "packages/decepticon/tests"
    for item in items:
        if root / "live" in item.path.parents:
            item.add_marker(pytest.mark.live)
        elif root / "integration" in item.path.parents:
            item.add_marker(pytest.mark.integration)
