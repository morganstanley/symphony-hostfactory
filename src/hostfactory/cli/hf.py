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

Implements the Symphony HostFactory provider required interfaces.
"""

import json
import logging
import pathlib
import sys

import click
import kubernetes
import urllib3

import hostfactory
from hostfactory import api as hfapi
from hostfactory import cli
from hostfactory import events as hfevents
from hostfactory import hfcleaner
from hostfactory import k8sutils
from hostfactory.cli import context
from hostfactory.cli import log_handler
from hostfactory.impl.watchers import kube_watcher
from hostfactory.impl.watchers import node_watcher
from hostfactory.impl.watchers import pod_watcher
from hostfactory.impl.watchers import request
from hostfactory.impl.watchers import request_machine
from hostfactory.impl.watchers import return_machine

ON_EXCEPTIONS = hostfactory.handle_exceptions(
    [
        (
            kubernetes.client.exceptions.ApiException,
            None,
        ),
        (urllib3.exceptions.ReadTimeoutError, None),
        (urllib3.exceptions.ProtocolError, None),
    ]
)

logger = logging.getLogger(__name__)


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
    "--log-file",
    default=None,
    envvar="HF_K8S_LOG_FILE",
    help="Hostfactory log file location.",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, writable=True),
)
@click.option(
    "--workdir",
    default=context.GLOBAL.default_workdir,
    envvar="HF_K8S_WORKDIR",
    help="Hostfactory working directory.",
)
@click.option(
    "--confdir",
    envvar="HF_K8S_PROVIDER_CONFDIR",
    help="Hostfactory config directory location.",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
@click.option(
    "--request-id",
    required=False,
    envvar="HF_K8S_REQUEST_ID",
    help="Request ID for the request.",
    type=str,
)
@ON_EXCEPTIONS
def run(proxy, log_level, log_file, workdir, confdir, request_id) -> None:  # noqa PLR0913
    """Entry point for the hostfactory command group.
    Example usage:
    $ hostfactory request-machines <json_file>

    Args:
        proxy (str): The proxy URL to access the K8s API.
        log_level (str): The log level to set.
        log_file (str): The log file location.
        workdir (str): The working directory.
        confdir (str): The configuration directory.
        machine_timeout (int): The machine timeout in minutes.
    """
    context.GLOBAL.workdir = workdir
    context.GLOBAL.proxy = proxy
    context.GLOBAL.dirname = "/".join([workdir, "events"])
    if confdir:
        context.GLOBAL.templates_path = "/".join(
            [confdir, context.GLOBAL.default_templates_filename]
        )

    if not request_id:
        context.GLOBAL.request_id = hostfactory.generate_short_uuid()
    else:
        context.GLOBAL.request_id = request_id

    log_handler.setup_logging(log_level, log_file)

    logger.info("Workdir: %s", workdir)
    for dirname in [
        "events",
        "kube-events",
        "nodes",
        "pods",
        "pods-status",
        "requests",
        "return-requests",
    ]:
        pathlib.Path(context.GLOBAL.workdir, dirname).mkdir(parents=True, exist_ok=True)


@run.command()
def get_available_templates() -> None:
    """Get available hostfactory templates."""
    if not context.GLOBAL.templates_path:
        raise click.UsageError(
            'Option "hostfactory --confdir" '
            'or envvar "HF_K8S_PROVIDER_CONFDIR" is required.'
        )

    response = hfapi.get_available_templates(context.GLOBAL.templates_path)
    logger.debug("get-available-templates response: %s", response)
    cli.output(json.dumps(response, indent=4))


def validate_request_file(ctx, param, file_obj):
    """Validate the request JSON file input."""
    logger.debug(
        "Validating param [%s] with value [%s] in context %s", param.name, file_obj, ctx
    )
    try:
        content = json.load(file_obj)
        if not isinstance(content, dict):
            raise click.BadParameter("Input must be a JSON object")
        if "template" not in content:
            raise click.BadParameter("Missing 'template' field")
        if not isinstance(content["template"], dict):
            raise click.BadParameter("'template' must be an object")
        if "machineCount" not in content["template"]:
            raise click.BadParameter("Missing 'machineCount' in template")
        if "templateId" not in content["template"]:
            raise click.BadParameter("Missing 'templateId' in template")
        return content
    except json.JSONDecodeError as exc:
        raise click.BadParameter("Invalid JSON format") from exc


@run.command()
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
    callback=validate_request_file,
)
@ON_EXCEPTIONS
def request_machines(json_file) -> None:
    """Request machines based on the provided hostfactory input JSON file."""
    if not context.GLOBAL.templates_path:
        raise click.UsageError(
            'Option "hostfactory --confdir" '
            'or envvar "HF_K8S_PROVIDER_CONFDIR" is required.'
        )
    logger.debug("request_machines: %s", json_file)

    count = json_file["template"]["machineCount"]
    template_id = json_file["template"]["templateId"]

    response = hfapi.request_machines(
        context.GLOBAL.workdir,
        context.GLOBAL.templates_path,
        template_id,
        count,
        context.GLOBAL.request_id,
    )

    logger.debug("request-machines response: %s", response)

    cli.output(json.dumps(response, indent=4))


@run.command()
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
)
@ON_EXCEPTIONS
def request_return_machines(json_file) -> None:
    """Request to return machines based on the provided hostfactory input JSON."""
    request = json.load(json_file)
    logger.info("request_return_machines: %s", request)
    machines = request["machines"]

    response = hfapi.request_return_machines(
        context.GLOBAL.workdir, machines, context.GLOBAL.request_id
    )

    logger.debug("request-return-machines Response: %s", response)
    cli.output(json.dumps(response, indent=4))


@run.command()
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
)
@ON_EXCEPTIONS
def get_request_status(json_file) -> None:
    """Get the status of hostfactory requests."""
    request = json.load(json_file)
    logger.info("get_request_status: %s", request)

    hf_req_ids = [req["requestId"] for req in request["requests"]]

    response = hfapi.get_request_status(context.GLOBAL.workdir, hf_req_ids)

    logger.debug("get-request-status response: %s", response)
    cli.output(json.dumps(response, indent=4))


@run.command()
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
)
@ON_EXCEPTIONS
def get_return_requests(json_file) -> None:
    """Get the status of CSP claimed hosts."""
    request = json.load(json_file)

    logger.info("get_return_requests: %s", request)
    machines = request["machines"]
    response = hfapi.get_return_requests(context.GLOBAL.workdir, machines)

    logger.debug("get-return-requests response: %s", response)
    cli.output(json.dumps(response, indent=4))


@run.command()
@click.option(
    "--timeout",
    help="Pod timeout in seconds.",
    type=int,
    default=300,
    show_default=True,
)
@click.option(
    "--run-once",
    help="Run the cleaner only once.",
    is_flag=True,
)
@click.option(
    "--dry-run",
    help="Dry run the cleaner.",
    is_flag=True,
)
@ON_EXCEPTIONS
def run_cleaner(timeout, run_once, dry_run) -> None:
    """Run the hostfactory cleaner."""
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Running cleaner at %s", workdir)
    k8s_client = k8sutils.get_kubernetes_client()
    hfcleaner.run(
        k8s_client,
        workdir,
        timeout,
        None if run_once else context.GLOBAL.cleanup_interval,
        dry_run,
        context.GLOBAL.node_refresh_interval,
    )


@run.group()
def watch() -> None:
    """Watch hostfactory events."""


@watch.command(name="pods")
@ON_EXCEPTIONS
def watch_pods() -> None:
    """Watch hostfactory pods."""
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Watching for hf k8s pods at %s", workdir)
    pod_watcher.watch(pathlib.Path(workdir))


@watch.command(name="request-machines")
@ON_EXCEPTIONS
def watch_request_machines() -> None:
    """Watch for machine requests."""
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Watching for hf request-machines at %s", workdir)
    k8s_client = k8sutils.get_kubernetes_client()
    request.watch(
        request_dir=pathlib.Path(workdir) / "requests",
        k8s_client=k8s_client,
        workdir=workdir,
        request_handler=request_machine.handle_machine,
    )


@watch.command(name="request-return-machines")
@ON_EXCEPTIONS
def watch_request_return_machines() -> None:
    """Watch for return machine requests."""
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Watching for hf request-return-machines at %s", workdir)
    k8s_client = k8sutils.get_kubernetes_client()
    request.watch(
        request_dir=pathlib.Path(workdir) / "return-requests",
        k8s_client=k8s_client,
        workdir=workdir,
        request_handler=return_machine.handle_machine,
    )


@watch.command(name="events")
@click.option("--dbfile", help="Events database file.")
@ON_EXCEPTIONS
def events(dbfile) -> None:
    """Watch for hostfactory events."""
    workdir = context.GLOBAL.workdir
    if not dbfile:
        dbfile = pathlib.Path(workdir) / "events.db"

    dirname = pathlib.Path(workdir) / "events"
    dirname.mkdir(parents=True, exist_ok=True)

    context.GLOBAL.dirname = str(dirname)
    context.GLOBAL.dbfile = dbfile

    logger.info("Watching for hf events at %s", dirname)
    hfevents.process_events()


@watch.command()
@ON_EXCEPTIONS
def nodes() -> None:
    """Watch for hostfactory nodes."""
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Watching for hf k8s nodes at %s", workdir)
    node_watcher.watch(workdir=pathlib.Path(workdir))


@watch.command()
@ON_EXCEPTIONS
def kube_events() -> None:
    """Watch for kubernetes events."""
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Watching for k8s nodes at %s", workdir)
    kube_watcher.watch(workdir=pathlib.Path(workdir))
