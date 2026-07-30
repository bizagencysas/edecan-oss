from importlib.metadata import version

from edecan_api import __version__


def test_api_version_comes_from_distribution_metadata() -> None:
    assert __version__ == version("edecan-api")
