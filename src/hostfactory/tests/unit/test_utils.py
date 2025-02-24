"""Test utility functions."""

import os
import pathlib
import shutil
import tempfile

import hostfactory


def test_atomic_symlink() -> None:
    """Tests creation of symlink."""
    workdir = tempfile.mkdtemp()
    link = os.path.join(workdir, "1")  # noqa: PTH118

    hostfactory.atomic_symlink("/foo/bar", link)
    assert os.readlink(link) == "/foo/bar"  # noqa: S101, PTH115

    hostfactory.atomic_symlink("/foo/baz", link)
    assert os.readlink(link) == "/foo/baz"  # noqa: S101, PTH115

    os.unlink(link)  # noqa: PTH108

    pathlib.Path(link).touch()
    hostfactory.atomic_symlink("/foo/baz", link)
    assert os.readlink(link) == "/foo/baz"  # noqa: S101, PTH115

    shutil.rmtree(workdir)
