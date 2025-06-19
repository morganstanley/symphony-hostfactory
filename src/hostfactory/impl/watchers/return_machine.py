"""Return machine watcher."""

import logging
from http import HTTPStatus

import kubernetes

from hostfactory import fsutils
from hostfactory import k8sutils

logger = logging.getLogger(__name__)


def _delete_pod(k8s_client, pod_name) -> None:
    """Delete pod."""
    logger.info("Deleting pod with client: %s", pod_name)
    logger.info("instance type %s", type(k8s_client))
    namespace = k8sutils.get_namespace()
    try:
        k8s_client.delete_namespaced_pod(pod_name, namespace)
        logger.info("Deleted pod with client: %s", pod_name)
    except kubernetes.client.rest.ApiException as exc:
        if exc.status == HTTPStatus.NOT_FOUND:
            # Assume the pod is already deleted if not found
            logger.info("Pod not found: %s", pod_name)
            return
        logger.info("Failed deleting the pod %s with %s", pod_name, exc)
        raise exc


def handle_machine(k8s_client, workdir, machine) -> None:
    """Return machine."""
    pod_name = machine.name
    pod_status = fsutils.fetch_pod_status(workdir, pod_name)
    logger.info("Returning machine: %s with status %s", pod_name, pod_status)

    if pod_status is None:
        logger.info("Unknown pod, skipping: %s", pod_name)
        return

    if pod_status == "deleted":
        logger.info("Pod already deleted: %s", pod_name)
        return

    _delete_pod(k8s_client, pod_name)
