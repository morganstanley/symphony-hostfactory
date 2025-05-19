"""Request machine watcher"""

import logging

import yaml

from hostfactory import fsutils
from hostfactory import k8sutils

logger = logging.getLogger(__name__)


def _create_pod(k8s_client, machine) -> None:
    """Create pod."""
    pod_name = machine.name
    logger.info("Creating pod: %s", pod_name)
    namespace = k8sutils.get_namespace()
    with machine.open("r") as file:
        logger.info("Pod spec file is: %s", file)
        pod_tpl = yaml.safe_load(file)

    req_id = machine.parent.name
    pod_tpl["metadata"]["name"] = pod_name
    pod_tpl["metadata"]["labels"]["app"] = pod_name
    pod_tpl["metadata"]["labels"]["symphony/hostfactory-reqid"] = req_id

    # TODO: Handle pod creation exceptions
    logger.info("Calling k8s API to create pod: %s", pod_name)
    result = k8s_client.create_namespaced_pod(namespace=namespace, body=pod_tpl)
    logger.info("Result of pod creation: %s", result)


# (andreik): I do not like implementation of the function.
#
# In particular, i do not like the pattern of modifying symlink into
# not symlink to mean something.
#
# Rather:
# - we start with machine being a symlink to a podspec file.
# - do try/except block. In the try block, we read the machine file. it it
#   is pointing to the podspec, it succeeds and all good. Once done,
#   rename the link to "created" - it will be broken, so next time
#   will never create the pod again.

# (cyriaqum): I do not see the point of the change
# the current flow in the pod directory is:
#  - Pod does not exist
#  - Request is sent to k8s
#  - Pod has a created simlink
#  - Kube_event updates the pod status
# This is already done in the pod-spec directory, this would just create a duplicate.
# The goal of the pods directory is to match the status in the kube cluster
# Is there a race condition as we call created after the pod creation.


def handle_machine_better(k8s_client, workdir, machine) -> None:
    """Create machine."""
    logger.info("Create machine: %s from workdir %s", machine, workdir)
    try:
        _create_pod(k8s_client, machine)
        fsutils.atomic_symlink(machine, "created")
    except FileNotFoundError:
        # Assume the pod is already created if not found
        logger.info("Pod already created: %s", machine.name)


def handle_machine(k8s_client, workdir, machine) -> None:
    """Create machine."""
    logger.info("Create machine: %s from workdir %s", machine, workdir)
    # Initially, machine is a symlink to a podspec file.
    # If it is not, that means it's been already processed.
    # Which means, we rerun a partially processed request dir.
    if machine.is_symlink():
        _create_pod(k8s_client, machine)
        # Flip the softlink into an empty file to mark it's done with.
        fsutils.atomic_create_empty(machine)
    else:
        logger.info("%s is not a symlink, skipping", machine)
