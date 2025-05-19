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

import logging
import pathlib
import time
from datetime import datetime
from http import HTTPStatus

import kubernetes

from hostfactory import events as hfevents
from hostfactory import fsutils
from hostfactory import k8sutils

logger = logging.getLogger(__name__)


# FIXIT: duplicate code exists in watcher.
# This should probably live in k8sutils
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
            return

    logger.info("Deleted pod: %s", pod_name)


# FIXIT: This should probably live in k8sutils
def _refresh_nodes(workdir: pathlib.Path, k8s_client, dry_run) -> None:
    """Get the list of nodes in cluster and save it in /nodes"""
    logger.info("Refreshing nodes")
    nodesdir = workdir / "nodes"

    expected_nodes = {
        node.name for node in nodesdir.iterdir() if not node.name.startswith(".")
    }

    try:
        actual_nodes = set()
        for node in k8s_client.list_node().items:
            node_name = node.metadata.name
            if not dry_run:
                fsutils.atomic_write(node, nodesdir / node_name)
            actual_nodes.add(node_name)
            if node_name not in expected_nodes:
                logger.info("Adding new node: %s", node)

        for node in expected_nodes - actual_nodes:
            logger.info("Removing stale node: %s", node)
            if not dry_run:
                (nodesdir / node).unlink(missing_ok=True)
    except kubernetes.client.rest.ApiException as exc:
        logger.exception("Could not get list of nodes: %s", exc)


def _is_timeout_reached(pod_ctime: datetime, timeout: int) -> bool:
    """Check if the timeout is reached"""
    current_time = datetime.now()
    elapsed_time = current_time - pod_ctime
    return elapsed_time.total_seconds() > timeout


def run(  # noqa: PLR0913
    k8s_client,
    workdir,
    timeout,
    run_interval=30,
    dry_run=False,
    node_refresh_interval=None,
):
    """Run the cleanup process"""
    workdir_path = pathlib.Path(workdir)
    hf_pods_status = workdir_path / "pods-status"
    last_node_refresh = None
    node_refresh_interval = node_refresh_interval or 300

    while True:
        if (
            last_node_refresh is None
            or (datetime.now() - last_node_refresh).total_seconds()
            > node_refresh_interval
        ):
            last_node_refresh = datetime.now()
            _refresh_nodes(workdir_path, k8s_client, dry_run)

        for pod in hf_pods_status.iterdir():
            if pod.name.startswith(".") or not pod.is_symlink():
                continue

            pod_ctime = datetime.fromtimestamp(pod.lstat().st_ctime)
            pod_status = pod.readlink().name

            with hfevents.EventsBuffer() as events:
                if pod_status in [
                    "creating",
                    "pending",
                ] and _is_timeout_reached(pod_ctime, timeout):
                    logger.info("Pod %s timed out.", pod.name)

                    events.post(
                        category="pod",
                        id=pod.name,
                        status="timeout",
                    )

                    if dry_run:
                        logger.info("Dry-run: Would have deleted pod %s", pod.name)
                        continue

                    _delete_k8s_pod(pod.name, k8s_client)
                    fsutils.atomic_symlink("deleted", pod)

        if run_interval is None:
            break

        time.sleep(run_interval)
