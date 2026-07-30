from importlib.metadata import version

from edecan_local import __version__


def test_local_version_comes_from_distribution_metadata() -> None:
    assert __version__ == version("edecan-local")
