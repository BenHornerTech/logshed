import sys
from pathlib import Path


def test_python_version():
    """Verify runtime Python version is at least 3.12."""
    assert sys.version_info >= (3, 12), f"Expected Python >= 3.12, got {sys.version_info}"


def test_directory_skeleton():
    """Verify standard backend directory skeleton and key module files exist."""
    backend_dir = Path(__file__).resolve().parent.parent
    app_dir = backend_dir / "app"

    expected_directories = [
        app_dir / "api",
        app_dir / "core",
        app_dir / "collectors",
        app_dir / "services",
        backend_dir / "tests",
    ]

    expected_files = [
        app_dir / "main.py",
        app_dir / "models.py",
    ]

    for d in expected_directories:
        assert d.is_dir(), f"Expected directory {d} to exist"

    for f in expected_files:
        assert f.is_file(), f"Expected file {f} to exist"
