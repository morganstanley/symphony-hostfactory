"""File system utils"""

import json
import logging
import os
import pathlib
import tempfile

from hostfactory import DateTimeEncoder

logger = logging.getLogger(__name__)


def atomic_symlink(src, dst):
    """Atomically create a symlink from `src` to `dst`."""
    if not src or not dst:
        raise ValueError("Source and destination paths must be provided.")
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".",
            dir=pathlib.Path(dst).parent,
        ) as tf:
            temp_path = tf.name
        os.symlink(src, temp_path)
        pathlib.Path(temp_path).rename(dst)
    except OSError as exc:
        logger.exception("Exception occurred: %s", exc)
        raise RuntimeError from exc

    return dst


def fetch_pod_status(workdir: pathlib.Path, pod: str) -> str | None:
    """Check the status of a pod on the filesystem.
    This should always be a symlink. If it is not, we have an issue.
    Updating the status of the pod is done either by creating/deleting the pod
    """
    pod_status_path = pathlib.Path(f"{workdir}/pods-status/{pod}")
    try:
        return pod_status_path.readlink().name
    except FileNotFoundError:
        logger.info("Pod status file not found: %s", pod_status_path)
    except OSError as exc:
        # pod-status can only be a symlink, if it is not, we have an issue
        logger.info("Pod status file is not a symlink: %s", pod_status_path)
        raise exc

    return None


def atomic_write(data, dst):
    """Atomically create a file with given content"""
    if not dst:
        logger.debug("No destination path provided. Skipping write.")
        return
    if isinstance(data, bytes):
        content = data
    elif isinstance(data, str):
        content = data.encode("utf-8")
    else:
        content = json.dumps(data, cls=DateTimeEncoder).encode("utf-8")

    try:
        with tempfile.NamedTemporaryFile(
            prefix=".",
            dir=pathlib.Path(dst).parent,
            delete=False,
        ) as tf:
            tf.write(content)
            tf.flush()
            pathlib.Path(tf.name).rename(dst)
    except OSError as exc:
        logger.exception("Exception occurred: %s", exc)
        raise RuntimeError from exc


def atomic_create_empty(dst):
    """Atomically create an empty file"""
    atomic_write(b"", dst)
