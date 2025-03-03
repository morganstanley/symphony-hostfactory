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

Common Test Utils
"""

import importlib
import json
import os
import pathlib
import tempfile

from jinja2 import Template


def get_pod_spec(flavor: str = "vanilla") -> str:
    """Returns the absolute path to the pod spec"""
    return str(
        importlib.resources.files("hostfactory.tests.resources").joinpath(
            f"{flavor}-spec.yml"
        )
    )


def generate_templates(flavor: str = "vanilla") -> str:
    """Creates a templates file and returns the path"""
    templates_tpl = importlib.resources.files("hostfactory.tests.resources").joinpath(
        f"{flavor}-templates.tpl"
    )

    pod_spec_path = get_pod_spec(flavor)

    with templates_tpl.open() as f:
        data = f.read()
        template_str = Template(data).render(podSpec=pod_spec_path)
        template_dict = json.loads(template_str)

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, suffix=".json"
    ) as json_file:
        json.dump(template_dict, json_file)
        return json_file.name


def get_workdir() -> str:
    """creates a tempdir for testing if HF_K8S_WORKDIR is not set"""
    temp_dir = os.getenv("HF_K8S_WORKDIR")
    if temp_dir:
        return temp_dir

    test_dir = pathlib.Path("/var/tmp/hostfactorytest/")  # noqa: S108
    test_dir.mkdir(parents=True, exist_ok=True)
    return str(test_dir)
