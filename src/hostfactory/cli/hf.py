"""Morgan Stanley makes this available to you under the Apache License, Version 2.0
(the "License"). You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0. See the NOTICE file distributed
with this work for additional information regarding copyright ownership.
Unless required by applicable law or agreed to in writing, software distributed
 under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
 CONDITIONS OF ANY KIND, either express or implied.  See the License for the
 specific language governing permissions and limitations under the License.

Implements the Symphony HostFactory provider required interfaces.
"""

import json
import logging
import pathlib
import sys

import click
import kubernetes

import hostfactory
from hostfactory import api as hfapi
from hostfactory import cli
from hostfactory import events as hfevents
from hostfactory import k8sutils
from hostfactory import watcher as hfwatcher
from hostfactory.cli import context
from hostfactory.cli import log_handler

logger = logging.getLogger(__name__)

ON_EXCEPTIONS = hostfactory.handle_exceptions(
    [
        (
            kubernetes.client.exceptions.ApiException,
            None,
        )
    ]
)

HF_K8S_WORKDIR_DEFAULT = "/tmp/hostfactory"  # noqa: S108


@click.group(name="hostfactory")
@click.option(
    "--proxy",
    help="Kubernetes API proxy URL.",
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
    "--workdir",
    default=HF_K8S_WORKDIR_DEFAULT,
    envvar="HF_K8S_WORKDIR",
    help="Hostfactory working directory.",
)
@click.pass_context
@ON_EXCEPTIONS
def run(ctx, proxy, log_level, workdir) -> None:
    """Entry point for the hostfactory command group.
    Example usage:
    $ hostfactory request-machines <json_file>

    Args:
        ctx (click.Context): The click context object.
        proxy (str): The proxy URL to access the K8s API.
        log_level (str): The log level to set.
    """
    log_handler.setup_logging(log_level=log_level)

    logger.debug(
        "A detailed log file can be found at: [b]%s[/b]",
        context.GLOBAL.logfile,
    )

    for dirname in ["requests", "return-requests", "pods", "nodes", "events"]:
        (pathlib.Path(workdir) / dirname).mkdir(parents=True, exist_ok=True)

    context.dirname = str(pathlib.Path(workdir) / "events")

    ctx.obj = {
        "workdir": workdir,
        "proxy": proxy,
    }


@run.command()
@click.pass_context
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
)
@ON_EXCEPTIONS
def request_machines(ctx, json_file) -> None:
    """Request machines based on the provided hostfactory input JSON file."""
    workdir = ctx.obj["workdir"]
    file_content = json_file.read().rstrip("\n")
    request = json.loads(file_content)
    logging.info("request_machines: %s", request)  # noqa: TID251

    # TODO: handle input validation
    count = request["template"]["machineCount"]

    response = hfapi.request_machines(workdir, count)

    logging.debug("request-machines response: %s", response)  # noqa: TID251

    cli.output(json.dumps(response, indent=4))


@run.command()
@click.pass_context
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
)
@ON_EXCEPTIONS
def request_return_machines(ctx, json_file) -> None:
    """Request to return machines based on the provided hostfactory input JSON."""
    workdir = ctx.obj["workdir"]
    request = json.load(json_file)
    logging.info("request_machines: %s", request)  # noqa: TID251
    machines = request["machines"]

    response = hfapi.request_return_machines(workdir, machines)

    logging.debug("request-return-machines Response: %s", response)  # noqa: TID251
    cli.output(json.dumps(response, indent=4))


@run.command()
@click.pass_context
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
)
@ON_EXCEPTIONS
def get_request_status(ctx, json_file) -> None:
    """Get the status of hostfactory requests."""
    workdir = ctx.obj["workdir"]
    request = json.load(json_file)
    logging.info("get_request_status: %s", request)  # noqa: TID251

    hf_req_ids = [req["requestId"] for req in request["requests"]]

    hfapi.get_request_status(workdir, hf_req_ids)


@run.command()
@click.pass_context
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
)
@ON_EXCEPTIONS
def get_return_requests(ctx, json_file) -> None:
    """Get the status of CSP claimed hosts."""
    workdir = ctx.obj["workdir"]
    request = json.load(json_file)

    logging.info("get_return_requests: %s", request)  # noqa: TID251
    machines = request["machines"]
    response = hfapi.get_return_requests(workdir, machines)

    logging.debug("get-return-requests response: %s", response)  # noqa: TID251
    cli.output(json.dumps(response, indent=4))


@run.group()
@click.pass_context
def watch(ctx) -> None:
    """Watch hostfactory events."""
    del ctx


@watch.command()
@click.pass_context
@ON_EXCEPTIONS
def pods(ctx) -> None:
    """Watch hostfactory pods."""
    workdir = ctx.obj["workdir"]
    k8sutils.load_k8s_config(ctx.obj["proxy"])
    logging.info("Watching for hf k8s pods at %s", workdir)  # noqa: TID251
    hfwatcher.watch_pods(workdir)


@watch.command(name="request-machines")
@click.pass_context
@click.option(
    "--pod-spec",
    required=True,
    envvar="HF_POD_SPEC",
    help="Path to the pod spec file. Can be set via HF_POD_SPEC env var.",
)
@ON_EXCEPTIONS
def watch_request_machines(ctx, pod_spec) -> None:
    """Watch for machine requests."""
    workdir = ctx.obj["workdir"]
    k8sutils.load_k8s_config(ctx.obj["proxy"])
    logging.info("Watching for hf request-machines at %s", workdir)  # noqa: TID251
    hfwatcher.watch_requests(workdir, pod_spec)


@watch.command(name="request-return-machines")
@click.pass_context
@ON_EXCEPTIONS
def watch_request_return_machines(ctx) -> None:
    """Watch for return machine requests."""
    workdir = ctx.obj["workdir"]
    k8sutils.load_k8s_config(ctx.obj["proxy"])
    logging.info("Watching for hf request-return-machines at %s", workdir)  # noqa: TID251
    hfwatcher.watch_return_requests(workdir)


@watch.command(name="events")
@click.option("--dbfile", help="Events database file.")
@click.pass_context
@ON_EXCEPTIONS
def events(ctx, dbfile) -> None:
    """Watch for hostfactory events."""
    if not dbfile:
        dbfile = pathlib.Path(ctx.obj["workdir"]) / "events.db"

    dirname = pathlib.Path(ctx.obj["workdir"]) / "events"
    dirname.mkdir(parents=True, exist_ok=True)

    context.dirname = str(dirname)
    context.dbfile = dbfile
    hfevents.init_events_db()

    hfevents.process_events()


@watch.command()
@click.pass_context
@ON_EXCEPTIONS
def nodes(ctx) -> None:
    """Watch for hostfactory nodes."""
    workdir = ctx.obj["workdir"]
    k8sutils.load_k8s_config(ctx.obj["proxy"])
    logging.info("Watching for hf k8s nodes at %s", workdir)  # noqa: TID251
    hfwatcher.watch_nodes(workdir)
