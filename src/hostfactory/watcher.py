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

Functions:
- watch_requests(workdir, pod_spec): Watch for machine requests
  and create pods.
- watch_return_requests(workdir): Watch for return machine requests.
  and delete pods.
- watch_pods(workdir): Watch the status of pods associated with hostfactory.
"""

import datetime
import json
import logging
import os
import pathlib
import tempfile
from http import HTTPStatus
from typing import Callable

import inotify.adapters
import kubernetes
import yaml

import hostfactory
from hostfactory import events as hfevents
from hostfactory import k8sutils

logger = logging.getLogger(__name__)

_HF_K8S_LABEL_KEY = "symphony/hostfactory-reqid"


def _process_pending_events(dirname, on_event, k8s_client) -> None:
    """Process all unfinished requests."""
    # TODO: consider removing .files in the cleanup

    for request in pathlib.Path(dirname).iterdir():
        if (
            request.is_dir()
            and not request.name.startswith(".")
            and not request.joinpath(".processed").exists()
        ):
            on_event(dirname, request.name, k8s_client)
            request.joinpath(".processed").touch()


def _watch_dir(dirname, on_event, k8s_client) -> None:
    """Watch directory for events, invoke callback on event."""
    _process_pending_events(dirname, on_event, k8s_client)

    dirwatch = inotify.adapters.Inotify()

    # Add the path to watch
    dirwatch.add_watch(
        str(dirname),
        mask=inotify.constants.IN_CREATE | inotify.constants.IN_MOVED_TO,
    )

    for event in dirwatch.event_gen(yield_nones=False):
        (_, _type_names, path, filename) = event
        if filename.startswith("."):
            continue
        # Ignore files, as each request is a directory.
        if not os.path.isdir(os.path.join(path, filename)):  # noqa: PTH112, PTH118
            continue
        # TODO: error handling? Exit on error and allow supvervisor to restart?

        on_event(path, filename, k8s_client)

        (pathlib.Path(path) / filename / ".processed").touch()


def _create_pod(machine, pod_spec, k8s_client) -> None:
    """Create pod."""
    logger.info("Creating pod for request: %s", machine.name)
    namespace = k8sutils.get_namespace()
    with pathlib.Path(pod_spec).open("r") as file:
        pod_tpl = yaml.safe_load(file)

    req_id = machine.parent.name
    pod_name = machine.name
    pod_tpl["metadata"]["name"] = pod_name
    pod_tpl["metadata"]["labels"]["app"] = pod_name
    pod_tpl["metadata"]["labels"]["symphony/hostfactory-reqid"] = req_id

    # TODO: Handle pod creation exceptions
    logger.info("Calling k8s API to create pod: %s", pod_name)
    result = k8s_client.create_namespaced_pod(namespace=namespace, body=pod_tpl)
    logger.info("Result of pod creation: %s", result)


def _delete_pod(pod_name, k8s_client) -> None:
    """Delete pod."""
    logger.info("Deleting pod: %s", pod_name)
    namespace = k8sutils.get_namespace()
    try:
        k8s_client.delete_namespaced_pod(
            pod_name, namespace, body=kubernetes.client.V1DeleteOptions()
        )
    except kubernetes.client.rest.ApiException as exc:
        if exc.status == HTTPStatus.NOT_FOUND:
            # Assume the pod is already deleted if not found
            logger.exception("Pod not found: %s", pod_name)


def _create_machine(request_dir, machine, k8s_client) -> None:
    """Create machine."""
    logger.info("Create machine: %s", machine)
    #
    # machine is a symlink to the podfile. Try open it, if it fails it
    # means that pod is not created yet.
    podspec_path = pathlib.Path(request_dir) / ".podspec"
    podfile = pathlib.Path(request_dir) / machine

    # Assumes validation is done and podsepc file exists
    # with correct path in the request directory.
    with podspec_path.open("r") as file:
        pod_spec = pathlib.Path(file.read())

    if not podfile.exists():
        logger.info("Pod file does not exist: %s", podfile)
        _create_pod(machine, pod_spec, k8s_client)


def _return_machine(request_dir, machine, k8s_client) -> None:
    """Return machine."""
    machine_name = machine.name
    podfile = pathlib.Path(request_dir) / machine_name
    deleted_pods_path = request_dir.parent / "deleted-pods"

    try:
        if (deleted_pods_path / machine_name).exists():
            logger.info("Pod already deleted: %s", machine_name)
            return

        with podfile.open("r") as file:
            pod = json.load(file)
            if pod["status"]["phase"].lower() in ["failed", "unknown"]:
                pass
            pod_name = pod["metadata"]["name"]
            logger.info("Return machine-pod: %s %s", machine.name, pod_name)
            _delete_pod(pod_name, k8s_client)
    except json.JSONDecodeError:
        # TODO: Handle case when podfile is not populated.
        logger.error("Failed to load JSON from podfile: %s", podfile)
    except FileNotFoundError:
        logger.error("Pod file does not exist: %s", podfile)


def _process_machine_request(dirname, filename, k8s_client) -> None:
    """Process machine request."""
    logger.info("Processing machine request: %s", filename)
    request_dir = pathlib.Path(dirname) / filename
    for machine in request_dir.iterdir():
        if machine.name.startswith("."):
            continue
        _create_machine(request_dir, machine, k8s_client)


def _process_return_machine_request(dirname, filename, k8s_client) -> None:
    """Process machine request."""
    logger.info("Processing return machine request: %s", filename)
    request_dir = pathlib.Path(dirname) / filename
    for machine in request_dir.iterdir():
        _return_machine(request_dir, machine, k8s_client)


def watch_requests(workdir, k8s_client) -> None:
    """Watch for machine requests."""
    dirname = pathlib.Path(workdir) / "requests"
    dirname.mkdir(parents=True, exist_ok=True)
    _watch_dir(str(dirname), _process_machine_request, k8s_client)


def watch_return_requests(workdir, k8s_client) -> None:
    """Watch for return machine requests."""
    dirname = pathlib.Path(workdir) / "return-requests"
    dirname.mkdir(parents=True, exist_ok=True)
    _watch_dir(str(dirname), _process_return_machine_request, k8s_client)


def _mark_deleted(eventdir, data) -> None:
    """Mark pod/node as deleted."""
    obj_name = data.metadata.name
    obj_file = pathlib.Path(eventdir) / obj_name
    if not obj_file.exists():
        logger.info("File does not exist: %s", obj_file)
        return
    eventdir_path = pathlib.Path(eventdir)
    deleted_obj_path = eventdir_path.parent / f"deleted-{eventdir_path.name}" / obj_name
    obj_file.replace(deleted_obj_path)
    hostfactory.atomic_symlink("deleted", obj_file)


def _put_event_data(eventdir, data) -> pathlib.Path:
    """Upsert event metadata to the eventdir."""
    if data.kind == "Event":
        event_filepath = pathlib.Path(
            eventdir, data.involved_object.kind, data.involved_object.name
        )
    else:
        event_filepath = pathlib.Path(eventdir, data.metadata.name)

    with tempfile.NamedTemporaryFile(delete=False, dir=eventdir) as tf:
        tf.write(
            json.dumps(
                data.to_dict(), cls=hostfactory.DateTimeEncoder, indent=4
            ).encode("utf-8")
        )
    temp_filepath = tf.name
    pathlib.Path(temp_filepath).rename(event_filepath)

    return event_filepath


def watch_pods(workdir) -> None:
    """Watch the status of pod associated with the hostfactory requests."""
    hfpodsdir = pathlib.Path(workdir) / "pods"
    namespace = k8sutils.get_namespace()
    k8sutils.watch_pods(
        label_selector=_HF_K8S_LABEL_KEY,
        handler=_make_event_handler(hfpodsdir, _push_pod_event),
        namespace=namespace,
    )


def watch_nodes(workdir) -> None:
    """Watch for node events."""
    hfnodesdir = pathlib.Path(workdir) / "nodes"
    k8sutils.watch_nodes(
        label_selector=None,
        handler=_make_event_handler(hfnodesdir, _push_node_event),
    )


def watch_kube_events(workdir) -> None:
    """Watch for kubernetes events."""
    hfeventsdir = pathlib.Path(workdir) / "kube-events"
    k8sutils.watch_kube_events(
        field_selector="involvedObject.kind=Node",
        handler=_make_event_handler(hfeventsdir, _push_kube_event),
    )


def _make_event_handler(eventdir, db_handler) -> Callable[[dict], None]:
    """Create a handler for events."""

    def _handler(event: dict) -> None:
        """Update the event status in the eventdir directory."""
        data = event["object"]
        if data.kind == "Event":
            involved_object = data.involved_object
            if not involved_object.name:
                logger.warning(
                    "Missing Involved object name. Skipping event %s.",
                    data.metadata.name,
                )
                return
            logger.info(
                "Event: %s %s %s %s",
                event["type"],
                data.metadata.name,
                involved_object.kind,
                involved_object.name,
            )
        else:
            logger.info("Event: %s %s %s", event["type"], data.kind, data.metadata.name)

        if event["type"] == "ERROR":
            logger.error("Error occurred while watching events: %s", data)
            return

        if event["type"] in ["ADDED", "MODIFIED"]:
            _put_event_data(eventdir, data)

        if event["type"] == "DELETED":
            _mark_deleted(eventdir, data)

        db_handler(event)

    return _handler


def _get_timestamp(data: datetime) -> int:
    """Get the ISO 8601 format timestamp."""
    return int(data.timestamp())


def _push_pod_event(event: dict) -> None:
    """Push pod event to db."""
    data = event["object"]
    pod_id = data.metadata.name

    with hfevents.EventsBuffer() as events:
        events.post(
            category="pod",
            id=pod_id,
            status=data.status.phase.lower(),
        )

        if data.metadata.creation_timestamp:
            events.post(
                category="pod",
                id=pod_id,
                status="created",
                timestamp=_get_timestamp(data.metadata.creation_timestamp),
            )

        if data.metadata.deletion_timestamp:
            events.post(
                category="pod",
                id=pod_id,
                status="deleted",
                timestamp=_get_timestamp(data.metadata.deletion_timestamp),
            )

        if data.spec.node_name:
            # FIXIT: this triggers a sync call to K8s API
            node_uid = k8sutils.get_node_uid(data.spec.node_name)
            events.post(
                category="pod",
                id=pod_id,
                node=str(data.spec.node_name),
                node_id=str(node_uid),
            )

        for condition in data.status.conditions or ():
            if condition.type == "PodScheduled" and condition.status == "True":
                events.post(
                    category="pod",
                    id=pod_id,
                    status="scheduled",
                    timestamp=_get_timestamp(condition.last_transition_time),
                )
            if condition.type == "Ready" and condition.status == "True":
                events.post(
                    category="pod",
                    id=pod_id,
                    status="ready",
                    timestamp=_get_timestamp(condition.last_transition_time),
                )
            if condition.type == "DisruptionTarget":
                events.post(
                    category="pod",
                    id=pod_id,
                    status="disrupted",
                    disrupted_reason=str(condition.reason),
                    disrupted_message=str(condition.message),
                    timestamp=_get_timestamp(condition.last_transition_time),
                )

        pod_cpu_core_request, pod_cpu_core_limit = k8sutils.get_total_pod_cpu(data)
        pod_memory_mib_request, pod_memory_mib_limit = k8sutils.get_total_pod_memory(
            data
        )

        events.post(
            category="pod",
            id=pod_id,
            cpu_requested=str(pod_cpu_core_request),
            cpu_limit=str(pod_cpu_core_limit),
            memory_requested=str(pod_memory_mib_request),
            memory_limit=str(pod_memory_mib_limit),
        )

        container_statuses = k8sutils.get_pod_container_statuses(data)
        if container_statuses:
            events.post(
                category="pod",
                id=pod_id,
                container_statuses=json.dumps(container_statuses),
            )


def _push_node_event(event: dict) -> None:
    """Push node event to db."""
    data = event["object"]
    # Why not use the node UUID only?
    node_id = f"{data.metadata.name}::{data.metadata.uid}"

    with hfevents.EventsBuffer() as events:
        if data.metadata.creation_timestamp:
            events.post(
                category="node",
                id=node_id,
                status="created",
                timestamp=_get_timestamp(data.metadata.creation_timestamp),
            )

        if data.metadata.deletion_timestamp:
            events.post(
                category="node",
                id=node_id,
                status="deleted",
                timestamp=_get_timestamp(data.metadata.deletion_timestamp),
            )

        for condition in data.status.conditions or ():
            if condition.type == "Ready" and condition.status == "True":
                events.post(
                    category="node",
                    id=node_id,
                    status="ready",
                    timestamp=_get_timestamp(condition.last_transition_time),
                )

        node_conditions = k8sutils.get_node_conditions(data)
        if node_conditions:
            events.post(category="node", id=node_id, conditions=node_conditions)

        cpu_parameters = k8sutils.get_node_cpu_resources(data)
        memory_parameters = k8sutils.get_node_memory_resources(data)

        events.post(
            category="node",
            id=node_id,
            cpu_capacity=str(cpu_parameters.get("capacity")),
            cpu_allocatable=str(cpu_parameters.get("allocatable")),
            memory_capacity=str(memory_parameters.get("capacity")),
            memory_allocatable=str(memory_parameters.get("allocatable")),
            cpu_reserved=str(cpu_parameters.get("reserved")),
            memory_reserved=str(memory_parameters.get("reserved")),
            zone=data.metadata.labels.get("topology.kubernetes.io/zone", None),
            region=data.metadata.labels.get("topology.kubernetes.io/region", None),
            node_size=data.metadata.labels.get(
                "node.kubernetes.io/instance-type", None
            ),
            capacity_type=data.metadata.labels.get("karpenter.sh/capacity-type", None),
        )


def _push_kube_event(event: dict) -> None:
    """Push kubernetes event to db."""
    data = event["object"]
    involved_object = data.involved_object
    if involved_object.name == involved_object.uid:
        logger.warning("Skipping event %s with missing object uid.", data.metadata.name)
        return
    if involved_object.kind != "Node":
        logger.warning("Skipping event %s that is not for node.", data.metadata.name)
        return

    node_id = f"{involved_object.name}::{involved_object.uid}"

    with hfevents.EventsBuffer() as events:
        parsed_node_event = k8sutils.parse_node_event(data)
        if parsed_node_event["message"] == "Disrupting Node: Underutilized/Delete":
            events.post(
                category="node",
                id=node_id,
                eviction_uderutilized=str(parsed_node_event["timestamp"]),
            )
        if parsed_node_event["message"] == "Disrupting Node: Empty/Delete":
            events.post(
                category="node",
                id=node_id,
                eviction_empty=str(parsed_node_event["timestamp"]),
            )
        events.post(
            category="node",
            id=node_id,
            events=json.dumps(parsed_node_event),
        )
