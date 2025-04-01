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

Cleanup/GC process to remove timed out machine requests and pods.
"""

import datetime
import json
import logging
import pathlib
import time
from http import HTTPStatus

import kubernetes

from hostfactory import atomic_symlink
from hostfactory import events as hfevents
from hostfactory import k8sutils

logger = logging.getLogger(__name__)


def _delete_k8s_pod(pod_name: str, k8s_client) -> None:
    """Delete a k8s pod"""
    namespace = k8sutils.get_namespace()
    try:
        k8s_client.delete_namespaced_pod(
            pod_name, namespace, body=kubernetes.client.V1DeleteOptions()
        )
    except kubernetes.client.rest.ApiException as exc:
        if exc.status == HTTPStatus.NOT_FOUND:
            logger.exception("Pod not found: %s", pod_name)

    logger.info("Deleted pod: %s", pod_name)


def _is_timeout_reached(pod_ctime: datetime.datetime, timeout: int) -> bool:
    """Check if the timeout is reached"""
    current_time = datetime.datetime.now()
    elapsed_time = current_time - pod_ctime
    return elapsed_time.total_seconds() > timeout


def run(k8s_client, workdir, timeout, run_once=False, dry_run=False):
    """Run the cleanup process"""
    workdir_path = pathlib.Path(workdir)
    hf_pods_path = workdir_path / "pods"

    while True:
        for pod in hf_pods_path.iterdir():
            pod_status = ""
            pod_ctime = datetime.datetime.fromtimestamp(pod.lstat().st_ctime)

            if pod.is_symlink():
                pod_status = pod.readlink().name
            else:
                with pod.open() as pod_file:
                    pod_json = json.load(pod_file)
                    pod_status = (
                        pod_json.get("status", {}).get("phase", "unknown").lower()
                    )

            if pod_status in [
                "creating",
                "pending",
            ] and _is_timeout_reached(pod_ctime, timeout):
                logger.info("Pod %s timed out.", pod.name)

                hfevents.post_event(
                    category="pod",
                    id=pod.name,
                    status="timeout",
                )
                if not dry_run:
                    # If the pod is not tracked by the pod watcher
                    if pod_status == "creating":
                        atomic_symlink("deleted", pod)
                    _delete_k8s_pod(pod.name, k8s_client)

        if run_once:
            break

        time.sleep(30)
