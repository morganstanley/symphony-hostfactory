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

Hostfactory admin CLI.
"""

import json
import logging
import pathlib

import click

from hostfactory import cli
from hostfactory import events
from hostfactory.cli import context
from hostfactory.cli import log_handler
from hostfactory.impl import hfadmin as impl

logger = logging.getLogger(__name__)


def _list_subdirectories_by_creation_time(directory) -> list:
    """Lists subdirectories in the given directory, ordered by creation time.

    :param directory: Path to the directory to list subdirectories from.
    :return: List of subdirectory paths ordered by creation time.
    """
    dir_path = pathlib.Path(directory)
    subdirs = [entry for entry in dir_path.iterdir() if entry.is_dir()]
    subdirs.sort(key=lambda subdir: subdir.stat().st_ctime)

    return [subdir.name for subdir in subdirs]


def _get_requests(workdir) -> list:
    """Get the list of requests.

    :param workdir: Working directory.
    :return: List of requests.
    """
    return _list_subdirectories_by_creation_time(workdir + "/requests")


def _get_return_requests(workdir) -> list:
    """Get the list of requests.

    :param workdir: Working directory.
    :return: List of requests.
    """
    return _list_subdirectories_by_creation_time(workdir + "/return-requests")


def _get_machines(workdir) -> list:
    """Get the list of machines.

    :param workdir: Working directory.
    :return: List of machines.
    """
    requests_dir = pathlib.Path(workdir + "/requests")
    machines = []

    for request_dir in requests_dir.iterdir():
        if request_dir.is_dir():
            machines.extend(
                [
                    entry.name
                    for entry in request_dir.iterdir()
                    if not entry.name.startswith(".")
                ]
            )

    return machines


@click.group(name="hostfactoryadmin")
@click.pass_context
@click.option(
    "--workdir",
    default=context.GLOBAL.default_workdir,
    envvar="HF_K8S_WORKDIR",
    help="Hostfactory working directory.",
)
@click.option(
    "--log-level",
    type=click.Choice(
        ["info", "debug", "error", "warning", "critical"], case_sensitive=False
    ),
    default="info",
    help="Set the log level.",
)
@click.option(
    "--log-file",
    default=None,
    envvar="HF_K8S_LOG_FILE",
    help="Hostfactory log file location.",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, writable=True),
)
def run(ctx, workdir, log_level, log_file) -> None:
    """Entry point for the hostfactoryadmin command group."""
    if not pathlib.Path(workdir).is_dir():
        raise ValueError("Invalid workdir: [%s] is not a directory.", workdir)

    ctx.obj = {"workdir": workdir}

    log_handler.setup_logging(log_level=log_level, log_file=log_file)


@run.command()
@click.pass_context
def list_machines(ctx) -> None:
    """List all machines."""
    workdir = ctx.obj.get("workdir")
    for _name in _get_machines(workdir):
        cli.output(_name)


@run.command()
@click.pass_context
def list_requests(ctx) -> None:
    """List all requests."""
    workdir = ctx.obj.get("workdir")
    for req in _get_requests(workdir):
        click.echo(req)


# TODO enum for pods states
@run.command()
@click.pass_context
@click.option(
    "--from-event",
    default="created",
    help="From event",
    type=click.Choice(["created", "running"]),
)
@click.option(
    "--to-event",
    default="running",
    help="To event",
    type=click.Choice(["running", "created"]),
)
def get_timings(ctx, from_event: str, to_event: str) -> None:
    """Get the timings of the requests."""
    workdir = ctx.obj["workdir"]

    average = events.event_average(workdir, event_from=from_event, event_to=to_event)
    cli.output(f"Average time between events: {average}")


@run.command()
@click.option("--template-id", default="Template-K8s-A", help="HF template id.")
@click.option("--count", default=1, help="Number of machines to create.")
def request_machines(template_id, count) -> None:
    """Request a machine."""
    data = {"template": {"templateId": template_id, "machineCount": count}}
    cli.output(json.dumps(data))


@run.command()
@click.argument("machines", nargs=-1)
def request_return_machines(machines) -> None:
    """Return a machine."""
    data = {
        "machines": [
            {
                "machineId": machine,
                "name": machine,
            }
            for machine in machines
        ]
    }
    cli.output(json.dumps(data))


@run.command()
@click.pass_context
@click.option("--return-requests", is_flag=True, help="Get status for return requests.")
def get_request_status(ctx, return_requests) -> None:
    """Get the status of a request."""
    workdir = ctx.obj.get("workdir")
    if return_requests:
        requests = _get_return_requests(workdir)
    else:
        requests = _get_requests(workdir)

    data = {"requests": [{"requestId": request} for request in requests]}
    cli.output(json.dumps(data))


@run.command()
@click.pass_context
def get_return_requests(ctx) -> None:
    """Get the status of a return request."""
    workdir = ctx.obj.get("workdir")
    data = {"machines": [{"name": entry} for entry in _get_machines(workdir) if entry]}
    cli.output(json.dumps(data))


@run.command()
@click.option("--count", default=1, help="The number of nodes to drain", type=int)
@click.option("--sleep", default=5, help="The time to wait between checks", type=int)
def delete_pods(count, sleep) -> None:
    """Delete all the pods on a node."""
    impl.delete_pods_in_namespace(pod_count=count, sleep_duration=sleep)
    cli.output(f"Deleted {count} pods")


@run.command()
@click.option("--count", default=1, help="The number of nodes to drain", type=int)
@click.option("--sleep", default=5, help="The time to wait between checks", type=int)
def drain_nodes(count, sleep) -> None:
    """Drain nodes."""
    deleted_count = impl.drain_node_in_namespace(node_count=count, sleep_duration=sleep)
    cli.output(f"Deleted {deleted_count} pods from nodes")
