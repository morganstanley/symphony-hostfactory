"""Morgan Stanley makes this available to you under the Apache License, Version 2.0
(the "License"). You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0. See the NOTICE file distributed
with this work for additional information regarding copyright ownership.
Unless required by applicable law or agreed to in writing, software distributed
 under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
 CONDITIONS OF ANY KIND, either express or implied.  See the License for the
 specific language governing permissions and limitations under the License.

Test Hostfactory implementation.
"""

# pylint: disable=duplicate-code

import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import click.testing

from hostfactory.cli.hf import run as hostfactory
from hostfactory.cli.hfadmin import run as hfadmin

UUID_PATTERN = r"[a-zA-Z0-9_]{12}"


# pylint: disable=protected-access


def _create_json_in(json_in, workdir):  # noqa: ANN202
    json_in = json.loads(json_in)
    with tempfile.NamedTemporaryFile(dir=workdir, mode="w", delete=False) as f:
        json.dump(json_in, f)
        f.flush()

    return f.name


def _run_cli(module, args):  # noqa: ANN202
    runner = click.testing.CliRunner()
    return runner.invoke(
        module,
        args,
        catch_exceptions=False,
    )


# TODO: Verify created file structure
@mock.patch("hostfactory.k8sutils.load_k8s_config", return_value=None)
@mock.patch("hostfactory.k8sutils.get_namespace", return_value="test-namespace")
class TestRequestMachines(unittest.TestCase):
    """Validate Hostfactory api functions"""

    def setUp(self) -> None:
        """Set up the test environment."""
        self.workdir = tempfile.mkdtemp(dir="/tmp")
        req_in = _run_cli(
            hfadmin,
            ["--workdir", self.workdir, "request-machines", "--count", 5],
        ).output
        self.json_in = _create_json_in(req_in, self.workdir)

    def tearDown(self) -> None:
        """Clean up the test environment."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_request_machines(self, _1, _2) -> None:  # noqa: PT019
        """Test case for the `request_machines` function.
        This test case verifies the behavior of the `request_machines` function
        by invoking it with a sample input and checking the output.
        """
        result = _run_cli(
            hostfactory,
            [
                "--workdir",
                self.workdir,
                "request-machines",
                self.json_in,
            ],
        )

        assert result.exit_code == 0  # noqa: S101

        # Assert that json output does not raise any errors
        json_output = json.loads(result.output)
        assert json_output is not None  # noqa: S101
        assert "message" in json_output  # noqa: S101
        assert "requestId" in json_output  # noqa: S101
        assert re.search(UUID_PATTERN, json_output.get("requestId"))  # noqa: S101
        reqid = json_output.get("requestId")
        # Check that events are generated.
        assert os.path.exists(f"{self.workdir}/events/pod~{reqid}-0~request~{reqid}")  # noqa: S101, PTH110


@mock.patch("hostfactory.k8sutils.load_k8s_config", return_value=None)
@mock.patch("hostfactory.k8sutils.get_namespace", return_value="test-namespace")
class TestRequestReturnMachines(unittest.TestCase):
    """Validate Hostfactory api functions"""

    def setUp(self) -> None:
        """Set up the test environment."""
        self.workdir = tempfile.mkdtemp(dir="/tmp")

        list_machines = ["bzzpube7599w-0", "bzzpube7599w-1", "cq7i8winzm4g-0"]
        req_in = _run_cli(
            hfadmin,
            [
                "--workdir",
                self.workdir,
                "request-return-machines",
                str(list_machines),
            ],
        ).output
        self.json_in = _create_json_in(req_in, self.workdir)

    def tearDown(self) -> None:
        """Clean up the test environment."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_request_return_machines(self, _1, _2) -> None:  # noqa: PT019
        """Test case for the `request_machines` function.
        This test case verifies the behavior of the `request_machines` function
        by invoking it with a sample input and checking the output.
        """
        result = _run_cli(
            hostfactory,
            [
                "--workdir",
                self.workdir,
                "request-return-machines",
                self.json_in,
            ],
        )

        assert result.exit_code == 0  # noqa: S101

        # Assert that json output does not raise any errors
        json_output = json.loads(result.output)
        assert json_output is not None  # noqa: S101
        assert "message" in json_output  # noqa: S101
        assert "requestId" in json_output  # noqa: S101
        assert re.search(UUID_PATTERN, json_output.get("requestId"))  # noqa: S101
