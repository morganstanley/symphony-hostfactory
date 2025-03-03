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

Low level hostfactory API.
"""

import json
import logging
import pathlib
import random
import string
import tempfile
import time
from typing import Tuple

import hostfactory
from hostfactory import events as hfevents
from hostfactory import validator as hfvalidator

_HF_K8S_LABEL_KEY = "symphony/hostfactory-reqid"

logger = logging.getLogger(__name__)


def _generate_short_uuid() -> str:
    """Generates a short UUID for hfreqid.
    Returns:
        str: A short UUID string of length 12.
    """
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choices(alphabet, k=12))


def _resolve_machine_status(pod, is_return_req) -> Tuple[str, str]:
    """Resolve the machine status based on the pod status.

    machine_result: Status of hf request related to this machine.
    Possible values:  executing, fail, succeed.

    machine_status : Status of machine.
    Expected values: running, stopped, terminated, shutting-down, stopping.
    """
    machine_results_map = {
        "pending": "succeed",
        "running": "succeed",
        "succeeded": "succeed",
        "failed": "fail",
        "unknown": "fail",
    }

    machine_status_map = {
        "pending": "running",
        "running": "running",
        "succeeded": "terminated",
        "failed": "terminated",
        "unknown": "terminated",
    }
    # TODO: capture all pod condition edge cases.
    pod_phase = pod["status"]["phase"].lower()

    machine_result = machine_results_map.get(pod_phase, "fail")
    machine_status = machine_status_map.get(pod_phase, "terminated")

    if is_return_req and machine_status == "terminated":
        machine_result = "succeed"

    return machine_status, machine_result


def _mktempdir(workdir) -> pathlib.Path:
    """Create a temporary directory in the workdir."""
    temp_dir = tempfile.mkdtemp(dir=workdir, prefix=".")
    return pathlib.Path(temp_dir)


def _get_templates(templates) -> dict:
    """Read and validate the templates file"""
    with pathlib.Path(templates).open("r") as file:
        data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError(
                "The templates file: %s must contain a JSON object", file.name
            )

    hfvalidator.validate(data)
    return data


def _write_podspec(tmp_path, templates, template_id) -> None:
    """Write the podspec file as part of the request."""
    templates_data = _get_templates(templates)["templates"]
    for t in templates_data:
        if t["templateId"] == template_id:
            template = t
            break
    else:
        raise ValueError("Template Id: %s not found in templates file.", template_id)

    podspec_path = tmp_path / ".podspec"
    podspec_path.write_text(template["podSpec"])


def get_available_templates(templates):
    """Validates and returns the hostfactory templates file."""
    logger.info("Getting available templates: %s", templates)

    return _get_templates(templates)


def request_machines(workdir, templates, template_id, count):
    """Request machines based on the provided hostfactory input JSON file.

    Generate unique hostfactory request id, create a directory for the request.

    For each machine requested, create a symlink in the request directory. The
    symlink is to non-existent "pending" file.

    """
    request_id = _generate_short_uuid()
    hfevents.post_events(
        [
            ("request", request_id, "begin_time", int(time.time())),
        ]
    )
    logger.info("HF Request ID: %s - Requesting machines: %s", request_id, count)

    # The request id is generated, directory should not exist.
    #
    # TODO(andreik): handle error if directory already exists.
    dst_path = pathlib.Path(workdir) / "requests" / request_id
    tmp_path = _mktempdir(workdir)
    _write_podspec(tmp_path, templates, template_id)

    for machine_id in range(count):
        machine = f"{request_id}-{machine_id}"
        hostfactory.atomic_symlink("pending", tmp_path / machine)

        hfevents.post_events(
            [
                ("pod", machine, "request", request_id),
                ("pod", machine, "requested", int(time.time())),
            ]
        )

    tmp_path.rename(dst_path)

    return {
        "message": "Success",
        "requestId": request_id,
    }


def get_request_status(workdir, hf_req_ids):
    """Get the status of hostfactory requests.

    For each request, first check if the request is a return request. If it is,
    look for machines in the return request directory. Otherwise, look for
    machines in the request directory.

    Machines are updated by the watcher. If machine is associated with the pod
    the symlink points to the pod info. Otherwise, the symlink points to
    non-existing "pending" file.

    For each request, request status is complete if all machines are in ready
    state. Otherwise, the request status is running. If any machine is in failed
    state, the status will be set to "complete_with_error".
    """
    # pylint: disable=too-many-locals

    hf_reqs_dir = pathlib.Path(workdir) / "requests"
    hf_return_reqs_dir = pathlib.Path(workdir) / "return-requests"
    events_to_post = []

    response = {"requests": []}

    logger.info("Getting request status: %s", hf_req_ids)

    state_running = 0b0001
    state_failed = 0b0010

    for request_id in hf_req_ids:
        machines = []

        # Assume successful requests status. It will be set to running and/or
        # failed based on the machines status.
        req_state = 0

        ret_request = True
        machines_dir = hf_return_reqs_dir / request_id
        if not machines_dir.exists():
            ret_request = False
            machines_dir = hf_reqs_dir / request_id

        if not machines_dir.exists():
            logger.error("Invalid request_id: %s", request_id)
            continue

        logger.debug("Checking machines in: %s", machines_dir)

        for file_path in machines_dir.iterdir():
            filename = file_path.name
            if filename.startswith("."):
                continue

            # Check if the machine is tracked by the watcher.
            # If not, assume the machine is in pending state.
            # TODO: Check if not a broken symlink.
            if file_path.is_symlink() and not file_path.resolve().is_file():
                logger.info("Pod file is not created yet: %s", file_path)
                req_state |= state_running
                continue

            with file_path.open("r", encoding="utf-8") as f:
                pod = json.load(f)
                if not pod["spec"]["node_name"] or pod["status"]["phase"] == "Pending":
                    req_state |= state_running
                    logger.info("Pod is not allocated to a node yet: %s", file_path)
                    continue

            machine_status, machine_result = _resolve_machine_status(pod, ret_request)
            if machine_result == "failed":
                req_state |= state_failed

            pod_name = pod["metadata"]["name"]
            pod_uid = pod["metadata"]["uid"]
            namespace = pod["metadata"]["namespace"]

            machine = {
                "machineId": pod_uid,
                "name": pod_name,
                "result": machine_result,
                "status": machine_status,
                "privateIpAddress": pod["status"]["pod_ip"],
                "publicIpAddress": "",
                "launchtime": pod["metadata"]["creation_timestamp"],
                "message": f"Allocated by K8s hostfactory - ns: {namespace}",
            }
            machines.append(machine)

        status = "running" if req_state & state_running else "complete"
        status = "complete_with_error" if req_state & state_failed else status

        req_status = {
            "requestId": request_id,
            "message": "",
            "status": status,
            "machines": machines,
        }

        response["requests"].append(req_status)

        event_type = "return" if ret_request else "request"
        events_to_post.append((event_type, request_id, "status", status))
        if status in ["complete", "complete_with_error"]:
            events_to_post.append(
                (event_type, request_id, "end_time", int(time.time()))
            )

    hfevents.post_events(events_to_post)
    return response


def request_return_machines(workdir, machines):
    """Request to return machines based on the provided hostfactory input JSON."""
    # TODO(andreik): duplicate code, create a function.
    hf_pods_dir = pathlib.Path(workdir) / "pods"
    hf_return_reqs_dir = pathlib.Path(workdir) / "return-requests"

    request_id = _generate_short_uuid()
    hfevents.post_events(
        [
            ("return", request_id, "begin_time", int(time.time())),
        ]
    )
    logger.info("Requesting to return machines: %s %s", request_id, machines)

    tmp_path = _mktempdir(workdir)
    dst_path = hf_return_reqs_dir / request_id

    for index, machine in enumerate(machines):
        machine_name = machine["name"]
        file_path = tmp_path / f"{request_id}-{index}"
        podfile = hf_pods_dir / machine_name
        if podfile.exists():
            hostfactory.atomic_symlink(podfile, file_path)
        else:
            logger.info("Machine not found with podfile: %s", podfile)

        hfevents.post_events(
            [
                ("pod", machine_name, "return_request", request_id),
                ("pod", machine_name, "returned", int(time.time())),
            ]
        )

    tmp_path.rename(dst_path)

    return {
        "message": "Machines returned.",
        "requestId": request_id,
    }


def get_return_requests(workdir, machines):
    """Get the status of CSP claimed hosts."""
    known = {machine["name"] for machine in machines}
    pods_dir = pathlib.Path(workdir) / "pods"
    actual = set()
    if pods_dir.exists():
        for file_path in pods_dir.iterdir():
            with file_path.open("r", encoding="utf-8") as f:
                pod = json.load(f)
                pod_phase = pod.get("status", {}).get("phase", "")
                if pod_phase and pod_phase.lower() not in ["unknown"]:
                    actual.add(pod["metadata"]["name"])
                else:
                    continue

    extra = known - actual

    response = {
        "status": "complete",
        "message": "Machines to be terminated."
        if extra
        else "Machines marked for termination retrieved successfully.",
        "requests": [{"gracePeriod": 0, "machine": machine} for machine in extra],
    }

    logger.debug("Machines to terminate: %r", extra)

    return response
