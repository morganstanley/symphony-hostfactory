"""Morgan Stanley makes this available to you under the Apache License,
Version 2.0 (the "License"). You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0. See the NOTICE file
distributed with this work for additional information regarding
copyright ownership. Unless required by applicable law or agreed
to in writing, software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
See the License for the specific language governing permissions and
limitations under the License. Watch and manage hostfactory machine
requests and pods in a Kubernetes cluster.

Top level functions for managing requests
"""

import logging
import pathlib

import inotify.adapters

from hostfactory import fsutils

logger = logging.getLogger(__name__)


def handle_request(
    workdir,
    request,
    request_handler,
) -> None:
    """Process event request."""
    logger.info("Processing event request: %s", request.name)
    for machine in fsutils.iterate_directory(directory=request):
        request_handler(workdir, machine)


def _process_pending_events(
    workdir,
    request_dir,
    request_handler,
) -> None:
    """Process all unfinished requests."""
    # TODO: consider removing .files in the cleanup

    for request in fsutils.iterate_directory(
        directory=request_dir, directories_only=True, exclude_dir_with_file=".processed"
    ):
        logger.info("Processing pending request: %s", request.name)
        handle_request(workdir, request, request_handler)
        request.joinpath(".processed").touch()


def watch(
    workdir,
    request_dir,
    request_handler,
) -> None:
    """Watch directory for events, invoke callback on event."""
    request_dir.mkdir(parents=True, exist_ok=True)

    _process_pending_events(
        workdir,
        request_dir,
        request_handler,
    )

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
            handle_request(
                workdir,
                request,
                request_handler,
            )
            request.joinpath(".processed").touch()
