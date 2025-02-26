"""Morgan Stanley makes this available to you under the Apache License, Version 2.0
(the "License"). You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0. See the NOTICE file distributed
with this work for additional information regarding copyright ownership.
Unless required by applicable law or agreed to in writing, software distributed
 under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
 CONDITIONS OF ANY KIND, either express or implied.  See the License for the
 specific language governing permissions and limitations under the License.

Common k8s helper functions.
"""

import logging
import os

import kubernetes


def _proxy_url():  # noqa: ANN202
    """Get the proxy URL from the environment.

    Returns:
        str: The proxy URL.
    """
    return os.environ.get("HTTP_PROXY", os.environ.get("http_proxy", None))


def is_inside_pod():
    """Check if the code is running inside a Kubernetes pod.

    Returns:
        bool: True if running inside a pod, False otherwise.
    """
    return os.path.exists("/var/run/secrets/kubernetes.io")  # noqa: PTH110


def load_k8s_config(proxy_url=None) -> None:
    """Load Kubernetes Credentials."""
    if is_inside_pod():
        # From Inside a Pod
        logging.debug("Loading cluster credentials.")  # noqa: TID251
        kubernetes.config.load_incluster_config()
    else:
        # Local kubeconfig file
        logging.debug("Loading local credentials.")  # noqa: TID251
        kubernetes.config.load_kube_config()

        # Proxy is set only for local config case. When running inside a pod,
        # proxy is not needed.
        if not proxy_url:
            proxy_url = _proxy_url()

        if proxy_url:
            logging.info("Setting proxy: %s", proxy_url)  # noqa: TID251
            # pylint: disable=protected-access
            kubernetes.client.Configuration._default.proxy = proxy_url  # noqa: SLF001


def get_namespace():
    """Retrieves the namespace based on the environment.

    Returns:
        str: The namespace to be used.
    """
    if is_inside_pod():
        with open(  # noqa: PTH123
            "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
            encoding="utf-8",
        ) as f:
            namespace = f.read().strip()
    else:
        # TODO: This assumes single namespace. Better solution to be
        #       explicit and require namespace to be set in the CLI opts.
        namespace = kubernetes.config.list_kube_config_contexts()[1]["context"][
            "namespace"
        ]

    return namespace


def watch_pods(label_selector, handler, namespace=None) -> None:
    """Watch for pods based on label selector."""
    if not namespace:
        namespace = get_namespace()
    resource_version = "0"
    while True:
        resource_version = _watch_pods(
            label_selector, handler, namespace, resource_version
        )
        if resource_version is None:
            break


def watch_nodes(label_selector, handler) -> None:
    """Watch for nodes based on label selector."""
    resource_version = "0"
    while True:
        resource_version = _watch_nodes(label_selector, handler, resource_version)
        if resource_version is None:
            break


def _watch_pods(label_selector, handler, namespace, resource_version):  # noqa: ANN202
    """Watch for pods based on label selector."""
    coreapi = kubernetes.client.CoreV1Api()
    watch = kubernetes.watch.Watch()
    return _watch_events(
        watch.stream(
            coreapi.list_namespaced_pod,
            namespace=namespace,
            label_selector=label_selector,
            resource_version=resource_version,
            timeout_seconds=0,
        ),
        handler,
    )


def _watch_nodes(label_selector, handler, resource_version):  # noqa: ANN202
    """Watch for nodes based on label selector."""
    coreapi = kubernetes.client.CoreV1Api()
    watch = kubernetes.watch.Watch()
    return _watch_events(
        watch.stream(
            coreapi.list_node,
            label_selector=label_selector,
            resource_version=resource_version,
            timeout_seconds=0,
        ),
        handler,
    )


def _watch_events(stream, handler):  # noqa: ANN202
    """Watch for events based on stream."""
    for event in stream:
        try:
            handler(event)
        except kubernetes.client.exceptions.ApiException as exc:  # noqa: PERF203
            if exc.status == 410:  # noqa: PLR2004
                resource_version = event["object"].metadata.resource_version
                logging.info(  # noqa: TID251
                    "Restarting watcher due to resourceVersion mismatch."
                    " Using resourceVersion: %s",
                    resource_version,
                )
                # Return the resource version to restart the watcher.
                return resource_version
            raise
    return None
