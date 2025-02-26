"""Morgan Stanley makes this available to you under the Apache License, Version 2.0
(the "License"). You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0. See the NOTICE file distributed
with this work for additional information regarding copyright ownership.
Unless required by applicable law or agreed to in writing, software distributed
 under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
 CONDITIONS OF ANY KIND, either express or implied.  See the License for the
 specific language governing permissions and limitations under the License.

Test configuration for regression testing
"""

from __future__ import annotations

import importlib
import logging
import os
import random
import tempfile
import threading
from functools import partial
from time import sleep

import click.testing
import kubernetes
import pytest
import yaml

from hostfactory.cli.hf import run as hostfactory
from hostfactory.cli.hfadmin import run as hfadmin
from hostfactory.events import event_average

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


# pylint: disable=redefined-outer-name

# TODO Manage kubeconfig setup for MKS, EKS and ASK


def pytest_collect_file(parent, file_path):
    """Collects the yaml test files"""
    if file_path.suffix == ".yaml" and file_path.name.startswith("test"):
        return YamlFile.from_parent(parent, path=file_path)

    return None


def get_workdir():
    """Returns the workdir for the tests."""
    return os.getenv("WORKDIR", "/var/tmp/hostfactory")  # noqa: S108


class YamlFile(pytest.File):
    """A test group to run from a yaml file."""

    def collect(self):  # noqa: D102
        yaml_tests = yaml.safe_load(self.path.open(encoding="utf-8"))
        logger.info("Raw spec is %s", yaml_tests)
        delete_pods_in_namespace()
        for test_case in yaml_tests:
            test_function = pytest.Function.from_parent(
                name=test_case["name"],
                parent=self,
                callobj=partial(run_custom_hostfactory_test, test_case),
            )
            test_function.add_marker(pytest.mark.regression)
            yield test_function

    # TODO Clean up after ourselves


def _run_cli(module: str, args: list) -> click.testing.Result:
    runner = click.testing.CliRunner()
    logger.info("Running %s with args %s.", module, args)

    result = runner.invoke(
        module,
        args,
        catch_exceptions=False,
        standalone_mode=False,
    )

    logger.info("Result of %s %s is %s", str(module), " ".join(args), result)

    return result


def run_hostfactory_command(command: str, json_in: str) -> click.testing.Result:
    """Run a hostfactory command"""
    logger.info("Json in is %s", json_in)

    with tempfile.NamedTemporaryFile(
        delete=False, mode="w", encoding="utf-8"
    ) as json_file:
        json_file.write(json_in)
        json_file.flush()
        logger.debug("Json is written to %s", json_file.name)
        result = _run_cli(
            hostfactory,
            ["--workdir", get_workdir(), command, str(json_file.name)],
        )

        logger.info("Returned hostfactory command.")
        assert result.exit_code == 0, result.output  # noqa: S101
        return result


def run_hostfactory_admin_command(command: str) -> click.testing.Result:
    """Run a hostfactory admin command"""
    result = _run_cli(hfadmin, ["--workdir", get_workdir(), *command.split(" ")])

    assert result.exit_code == 0  # noqa: S101
    assert result.output is not None, result.output  # noqa: S101
    logger.info("Hostfactory admin output is %s", result.output)

    return result


def run_pod_watch_command() -> click.testing.Result:
    """Run a pod watch command"""
    return _run_cli(hostfactory, ["--workdir", get_workdir(), "watch", "pods"])


def run_request_machine_command(pod_flavour: str = "vanilla") -> click.testing.Result:
    """Run a pod watch command"""
    pod_spec_path = get_pod_spec(pod_flavour)
    logger.info("Using pod spec %s", pod_spec_path)
    return _run_cli(
        hostfactory,
        [
            "--workdir",
            get_workdir(),
            "watch",
            "request-machines",
            "--pod-spec",
            pod_spec_path,
        ],
    )


def get_pod_spec(flavor: str = "vanilla") -> str:
    """Returns the absolute path to the pod spec"""
    return str(
        importlib.resources.files("hostfactory.tests.resources").joinpath(
            f"{flavor}-spec.yml"
        )
    )


def run_request_return_command() -> click.testing.Result:
    """Run a pod watch command"""
    return _run_cli(
        hostfactory,
        ["--workdir", get_workdir(), "watch", "request-return-machines"],
    )


def run_event_command() -> click.testing.Result:
    """Run a pod watch command"""
    return _run_cli(
        hostfactory,
        ["--workdir", get_workdir(), "watch", "events"],
    )


def run_custom_hostfactory_test( # noqa: C901, PLR0912
    test_spec: dict,
    run_hostfactory_pods,  # pylint: disable=unused-argument  # noqa: ARG001
    run_hostfactory_machines,  # noqa: ARG001
    run_hostfactory_events,  # noqa: ARG001
    run_hostfactory_returns,  # noqa: ARG001
) -> None:
    """Run a custom hostfactory test."""
    logger.info("Test spec is %s", test_spec)

    if "hostfactory-admin" in test_spec:
        args = ""
        if "request-return-machines" in test_spec["hostfactory-admin"]:
            # TODO consider do a return all piece of logic
            logger.info("Populating with the list of machines")
            machines = run_hostfactory_admin_command("list-machines").output.split()
            if "return_count" in test_spec:
                machines = random.sample(machines, test_spec["return_count"])
            args = " " + " ".join(machines)
        result = run_hostfactory_admin_command(test_spec["hostfactory-admin"] + args)
        json_in = result.output
        assert json_in is not None, result  # noqa: S101

    if "hostfactory" in test_spec:
        logger.info(
            "Json in is %s and hostfactory command is %s",
            json_in,
            test_spec["hostfactory"],
        )
        run_hostfactory_command(test_spec["hostfactory"], json_in)

    if "list-machines" in test_spec:
        limit = 10
        iteration = 0
        while (
            not run_hostfactory_admin_command("list-machines").output
            < test_spec["list-machines"]
            and iteration < limit
        ):
            sleep(10)
            logger.info("Waiting for pods to be reach expected count")
            iteration += 1
        if iteration >= limit:
            raise AssertionError("Pods did not reach expected count")
        if "timings" in test_spec["target"]:
            verify_timings(test_spec["target"]["timings"])

    if "drain_node" in test_spec:
        logger.info("Draining node")
        value = test_spec["drain_node"]
        for _ in range(value):
            drain_node_in_namespace()

    if "target" in test_spec:
        logger.info("Target is %s", test_spec["target"])
        limit = 10
        iteration = 0
        while not matches_pod_count(test_spec["target"]["pods"]) and iteration < limit:
            sleep(10)
            logger.info("Waiting for pods to be reach expected count")
            iteration += 1
        if iteration >= limit:
            raise AssertionError("Pods did not reach expected count")
        if "timings" in test_spec["target"]:
            verify_timings(test_spec["target"]["timings"])


def matches_pod_count(expected_pod_count: int) -> bool:
    """Check if the pod count matches the expected count."""
    current_pods = get_pods_in_current_namespace()
    current_pod_count = len(current_pods.items)
    logger.info("Current pod count is %s", current_pod_count)
    return current_pod_count == expected_pod_count


def verify_timings(expected_timings: dict) -> None:
    """Verifies the timings of the requests."""
    logger.info("Expected_timings are %s", expected_timings)
    for expected_timing in expected_timings:
        from_event = expected_timing["from"]
        to_event = expected_timing["to"]
        expected_average = expected_timing["average"]
        actual_average = event_average(
            get_workdir(), event_from=from_event, event_to=to_event
        )
        assert actual_average < expected_average  # noqa S101


def get_pods_in_current_namespace() -> kubernetes.client.models.V1PodList:
    """Get the pods in the current namespace"""
    # Load Kubernetes configuration
    kubernetes.config.load_kube_config()

    # Get the current namespace
    namespace = kubernetes.config.list_kube_config_contexts()[1]["context"]["namespace"]

    # Create an instance of the CoreV1Api
    core_v1_api = kubernetes.client.CoreV1Api()

    # List the pods in the current namespace
    return core_v1_api.list_namespaced_pod(namespace=namespace)


def delete_pods_in_namespace() -> None:
    """Clean up the namespace we are evolving in"""
    # Load Kubernetes configuration
    kubernetes.config.load_kube_config()

    # Get the current namespace
    namespace = kubernetes.config.list_kube_config_contexts()[1]["context"]["namespace"]

    # Create an instance of the CoreV1Api
    core_v1_api = kubernetes.client.CoreV1Api()

    # List the pods in the current namespace
    pods = core_v1_api.list_namespaced_pod(namespace=namespace)

    # Delete each pod
    for pod in pods.items:
        core_v1_api.delete_namespaced_pod(name=pod.metadata.name, namespace=namespace)
    while len(get_pods_in_current_namespace().items) != 0:
        # TODO sleep should be configurable
        sleep(5)
        logger.info("Waiting for namespace to clean up")


def drain_node_in_namespace() -> None:
    """Modeling draining a node as deleting all the pods running on a node.
    This picks a random node and delete all pods on it.
    """
    # Load Kubernetes configuration
    kubernetes.config.load_kube_config()

    # Get the current namespace
    namespace = kubernetes.config.list_kube_config_contexts()[1]["context"]["namespace"]

    # Create an instance of the CoreV1Api
    core_v1_api = kubernetes.client.CoreV1Api()

    # List the nodes in the current namespace
    nodes = core_v1_api.list_node()

    # Pick a random node
    node = random.choice(nodes.items)

    # List the pods in the current namespace
    pods = core_v1_api.list_namespaced_pod(namespace=namespace)

    # Delete each pod
    for pod in pods.items:
        if pod.spec.node_name == node.metadata.name:
            core_v1_api.delete_namespaced_pod(
                name=pod.metadata.name, namespace=namespace
            )
    while len(get_pods_in_current_namespace().items) != 0:
        # TODO sleep should be configurable
        sleep(5)
        logger.info("Waiting for namespace to clean up")


class PodWatcher:
    """Pod watcher class."""

    def __init__(self) -> None:  # noqa: D107
        self.output = None
        logger.info("In pod watcher init")

    def __enter__(self):  # noqa: ANN204, D105
        logger.info("In pod watcher enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: ANN204, D105
        logger.info("In pod watcher exit: %s", self.output)

    def run_pod_watcher(self):
        """Run the pod watcher."""
        logger.info("Starting pod watcher")
        result = run_pod_watch_command()
        logger.info("Stopping pod watched: %s", result.output)
        self.output = result.output
        return result


class EventsWatcher:
    """Event watcher class."""

    def __init__(self) -> None:  # noqa: D107
        self.output = None
        logger.info("In event watcher init")

    def __enter__(self):  # noqa: ANN204, D105
        logger.info("In event watcher enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: ANN204, D105
        logger.info("In event watcher exit: %s", self.output)

    def run_events_watcher(self):
        """Run the events watcher"""
        logger.info("Starting event watcher")
        result = run_event_command()
        logger.info("Stopping event watched: %s", result.output)
        self.output = result.output
        return result


class RequestMachineWatcher:
    """Request machine watcher class."""

    def __init__(self) -> None:  # noqa: D107
        self.output = None
        logger.info("In request machine watcher init")

    def __enter__(self):  # noqa: ANN204, D105
        logger.info("In request machine watcher enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: ANN204, D105
        logger.info("In request machine watcher exit: %s", self.output)

    def run_request_machine_watcher(self):
        """Run the request machine watcher."""
        logger.info("Starting request machine watcher")
        result = run_request_machine_command()
        logger.info("Stopping request machine watched: %s", result.output)
        self.output = result.output
        return result


class ReturnMachineWatcher:
    """Return machine watcher class."""

    def __init__(self) -> None:  # noqa: D107
        self.output = None
        logger.info("In return machine watcher init")

    def __enter__(self):  # noqa: ANN204, D105
        logger.info("In return machine watcher enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: ANN204, D105
        logger.info("In return machine watcher exit: %s", self.output)

    def run_request_return_watcher(self):
        """Run the request return watcher."""
        logger.info("Starting request return watcher")
        result = run_request_return_command()
        logger.info("Stopping request return watched: %s", result.output)
        self.output = result.output
        return result


@pytest.fixture(scope="session")
def run_hostfactory_returns():
    """Run the request-return-machines command."""
    logger.info("Running hostfactory return")
    return_machine_watcher = ReturnMachineWatcher()
    with return_machine_watcher:
        thread = threading.Thread(
            target=return_machine_watcher.run_request_return_watcher
        )
        thread.daemon = True
        thread.start()
        yield return_machine_watcher


@pytest.fixture(scope="session")
def run_hostfactory_pods():
    """Run the hostfactory pods."""
    logger.info("Running hostfactory pods")
    pod_watcher = PodWatcher()
    with pod_watcher:
        thread = threading.Thread(target=pod_watcher.run_pod_watcher)
        thread.daemon = True
        thread.start()
        yield pod_watcher

    logger.info("Closing hostfactory pods")


@pytest.fixture(scope="session")
def run_hostfactory_machines():
    """Run the hostfactory machines."""
    logger.info("Running request machine")
    request_machine_watcher = RequestMachineWatcher()
    with request_machine_watcher:
        thread = threading.Thread(
            target=request_machine_watcher.run_request_machine_watcher
        )
        thread.daemon = True
        thread.start()
        yield request_machine_watcher
    logger.info("Closing request machine")


@pytest.fixture(scope="session")
def run_hostfactory_events():
    """Run the hostfactory event reporting service"""
    logger.info("Running event watcher")
    events_watcher = EventsWatcher()
    with events_watcher:
        thread = threading.Thread(target=events_watcher.run_events_watcher)
        thread.daemon = True
        thread.start()
        yield events_watcher
    logger.info("Closing events watcher")
