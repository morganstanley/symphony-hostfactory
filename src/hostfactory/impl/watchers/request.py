"""Top level functions for managing requests"""

import logging
import pathlib

import inotify.adapters

logger = logging.getLogger(__name__)


def handle_request(k8s_client, workdir, request_handler, request):
    """Process machine request."""
    logger.info("Processing machine request: %s", request.name)
    for machine in request.iterdir():
        request_handler(k8s_client, workdir, machine)


def _process_pending_events(request_dir, k8s_client, workdir, request_handler) -> None:
    """Process all unfinished requests."""
    # TODO: consider removing .files in the cleanup

    for request in request_dir.iterdir():
        if (
            request.is_dir()
            and not request.name.startswith(".")
            and not request.joinpath(".processed").exists()
        ):
            handle_request(k8s_client, workdir, request_handler, request)
            request.joinpath(".processed").touch()


def watch(request_dir, k8s_client, workdir, request_handler) -> None:
    """Watch directory for events, invoke callback on event."""
    request_dir.mkdir(parents=True, exist_ok=True)

    _process_pending_events(request_dir, k8s_client, workdir, request_handler)

    dirwatch = inotify.adapters.Inotify()

    # Add the path to watch
    dirwatch.add_watch(
        str(request_dir),
        mask=inotify.constants.IN_CREATE | inotify.constants.IN_MOVED_TO,
    )

    for event in dirwatch.event_gen(yield_nones=False):
        (_, _type_names, path, filename) = event
        if filename.startswith("."):
            continue
        # Ignore files, as each request is a directory.
        request = pathlib.Path(path) / filename
        if request.is_dir():
            # TODO: error handling? Exit on error and allow supvervisor to restart?
            handle_request(k8s_client, workdir, request_handler, request)
            request.joinpath(".processed").touch()
