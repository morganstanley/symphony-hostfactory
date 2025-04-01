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
    return random.choice(string.ascii_lowercase) + "".join(
        random.choices(alphabet, k=11)
    )


def _load_pod_file(workdir: pathlib.Path, pod_name: str) -> dict:
    """Loads the pod file"""
    hf_pods_dir = workdir / "pods"
    deleted_hf_pods_dir = workdir / "deleted-pods"
    pod_path = hf_pods_dir / pod_name

    if pod_path.is_symlink():
        if pod_path.readlink().name == "deleted":
            pod_path = deleted_hf_pods_dir / pod_name
            # Assume the pod timed out.
            if not pod_path.exists():
                return {}
        elif pod_path.readlink().name == "creating":
            logger.info("Machine is still in creating state: %s", pod_name)
            return {}
        else:
            raise ValueError("Invalid symlink: %s", pod_path)

    logger.debug("Loading pod file: %s", pod_path)
    with pod_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _get_machines_dir(
    workdir: pathlib.Path, request_id: str
) -> Tuple[pathlib.Path, bool]:
    """Get the machines directory based on the request id."""
    hf_reqs_dir = workdir / "requests"
    hf_return_reqs_dir = workdir / "return-requests"

    is_return_req = False
    machines_dir = hf_reqs_dir / request_id
    if not machines_dir.exists():
        is_return_req = True
        machines_dir = hf_return_reqs_dir / request_id

    if not machines_dir.exists():
        raise FileNotFoundError(f"Request directory not found: {machines_dir}")

    return machines_dir, is_return_req


def _resolve_machine_status(pod_status: str, is_return_req: bool) -> Tuple[str, str]:
    """Resolve the machine status based on the pod status.

    machine_result: Status of hf request related to this machine.
    Possible values:  executing, fail, succeed.

    machine_status : Status of machine.
    Expected values: running, stopped, terminated, shutting-down, stopping.
    """
    if not pod_status:
        return "terminated", "fail"

    machine_results_map = {
        "creating": "executing",
        "pending": "executing",
        "running": "succeed",
        "succeeded": "succeed",
        "failed": "fail",
        "unknown": "fail",
        "deleted": "fail",
    }

    machine_status_map = {
        "creating": "running",
        "pending": "running",
        "running": "running",
        "succeeded": "terminated",
        "failed": "terminated",
        "unknown": "terminated",
        "deleted": "terminated",
    }

    machine_status = machine_status_map.get(pod_status, "terminated")
    machine_result = machine_results_map.get(pod_status, "fail")

    if is_return_req:
        machine_result = "succeed" if machine_status == "terminated" else "executing"

    return machine_status, machine_result


def _mktempdir(workdir: pathlib.Path) -> pathlib.Path:
    """Create a temporary directory in the workdir."""
    temp_dir = tempfile.mkdtemp(dir=str(workdir), prefix=".")
    return pathlib.Path(temp_dir)


def _get_templates(templates: pathlib.Path) -> dict:
    """Read and validate the templates file"""
    with templates.open("r") as file:
        data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError(
                "The templates file: %s must contain a JSON object", file.name
            )

    hfvalidator.validate(data)
    return data


def _write_podspec(
    tmp_path: pathlib.Path, templates: pathlib.Path, template_id: str
) -> None:
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

    return _get_templates(pathlib.Path(templates))


def request_machines(workdir, templates, template_id, count):
    """Request machines based on the provided hostfactory input JSON file.

    Generate unique hostfactory request id, create a directory for the request.

    For each machine requested, create a symlink in the request directory. The
    symlink is to non-existent "pending" file.

    """
    request_id = _generate_short_uuid()
    hfevents.post_event(
        category="request",
        id=request_id,
        count=count,
        template_id=template_id,
    )
    logger.info("HF Request ID: %s - Requesting machines: %s", request_id, count)

    # The request id is generated, directory should not exist.
    #
    # TODO(andreik): handle error if directory already exists.
    workdir_path = pathlib.Path(workdir)
    templates_path = pathlib.Path(templates)
    dst_path = workdir_path / "requests" / request_id
    tmp_path = _mktempdir(workdir_path)

    _write_podspec(tmp_path, templates_path, template_id)

    with hfevents.EventsBuffer() as events:
        for machine_id in range(count):
            machine = f"{request_id}-{machine_id}"

            podfile = workdir_path / "pods" / machine
            hostfactory.atomic_symlink(podfile, tmp_path / machine)
            hostfactory.atomic_symlink("creating", podfile)

            events.post(
                category="pod",
                id=machine,
                request_id=request_id,
                template_id=template_id,
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
    workdir_path = pathlib.Path(workdir)
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

        machines_dir, ret_request = _get_machines_dir(workdir_path, request_id)
        if not machines_dir.exists():
            logger.info("Request directory not found: %s", machines_dir)
            continue
        logger.debug("Checking machines in: %s", machines_dir)

        for file_path in machines_dir.iterdir():
            if file_path.name.startswith("."):
                continue

            # Check if the machine is tracked by the watcher.
            # If not, assume the machine is in pending state.
            # TODO: Check if not a broken symlink.

            podfile_path = file_path.readlink()
            podname = podfile_path.name

            pod_status = ""
            pod = _load_pod_file(workdir_path, podname)

            if podfile_path.is_symlink():
                pod_status = podfile_path.readlink().name
            elif podfile_path.exists():
                pod_status = pod.get("status", {}).get("phase", "unknown").lower()
            else:
                logger.info("Machine not found with podfile: %s", podfile_path)
                continue

            machine_status, machine_result = _resolve_machine_status(
                pod_status, ret_request
            )

            if machine_result == "executing":
                req_state |= state_running
                # Machine can be omitted if request is still executing.
                if not ret_request:
                    continue

            if machine_result == "fail":
                req_state |= state_failed

            machine = {
                "machineId": pod["metadata"]["uid"] if pod else "",
                "name": pod["metadata"]["name"] if pod else podname,
                "result": machine_result,
                "status": machine_status,
                "privateIpAddress": pod.get("status").get("pod_ip", "") if pod else "",
                "publicIpAddress": "",
                "launchtime": pod["metadata"]["creation_timestamp"] if pod else "",
                "message": "Allocated by K8s hostfactory",
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
        events_to_post.append(
            {
                "category": event_type,
                "id": request_id,
                "status": status,
            }
        )

    hfevents.post_events(events_to_post)
    return response


def request_return_machines(workdir, machines):
    """Request to return machines based on the provided hostfactory input JSON."""
    # TODO(andreik): duplicate code, create a function.
    workdir_path = pathlib.Path(workdir)
    hf_pods_dir = workdir_path / "pods"
    hf_return_reqs_dir = workdir_path / "return-requests"

    request_id = _generate_short_uuid()
    with hfevents.EventsBuffer() as events:
        events.post(
            category="return",
            id=request_id,
        )
        logger.info("Requesting to return machines: %s %s", request_id, machines)

        tmp_path = _mktempdir(workdir_path)
        dst_path = hf_return_reqs_dir / request_id

        for index, machine in enumerate(machines):
            machine_name = machine["name"]
            file_path = tmp_path / f"{request_id}-{index}"
            podfile = hf_pods_dir / machine_name
            if podfile.exists() or podfile.is_symlink():
                hostfactory.atomic_symlink(podfile, file_path)
            else:
                logger.info("Machine not found with podfile: %s", podfile)

            events.post(
                category="pod",
                id=machine_name,
                return_id=request_id,
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
            if file_path.name.startswith("."):
                continue
            if file_path.is_symlink() and file_path.readlink().name == "deleted":
                continue

            actual.add(file_path.name)
    else:
        raise FileNotFoundError(f"Pods directory not found: {pods_dir}")

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
