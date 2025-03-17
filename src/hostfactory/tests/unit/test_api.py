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

Test hostfactory api module.
"""

import json
import pathlib
from unittest import mock

import pytest

from hostfactory import api
from hostfactory.tests import generate_provider_conf
from hostfactory.tests import get_pod_spec
from hostfactory.tests import get_workdir

mock_pod = {
    "spec": {"node_name": "node1"},
    "status": {"pod_ip": "192.168.1.1", "phase": "Running"},
    "metadata": {
        "creation_timestamp": "1739212317",
        "name": "pod1",
        "uid": "uid1",
        "namespace": "n1",
    },
}

mock_hf_template = {
    "templates": [
        {
            "templateId": "mock_template_id",
            "maxNumber": 10,
            "attributes": {
                "nram": ["4096"],
                "ncpus": ["8"],
                "ncores": ["1"],
                "type": ["String", "X86_64"],
            },
            "podSpec": "pod-spec.yaml",
        }
    ]
}


@mock.patch(
    "hostfactory.api.tempfile.mkdtemp", return_value="/path/to/workdir/.tempdir"
)
def test_mktempdir(mock_mkdtemp) -> None:
    """Test mktempdir."""
    workdir = "/path/to/workdir"
    temp_dir = api._mktempdir(workdir)
    mock_mkdtemp.assert_called_once_with(dir=workdir, prefix=".")
    assert isinstance(temp_dir, pathlib.Path)
    assert str(temp_dir) == "/path/to/workdir/.tempdir"


def test_generate_short_uuid() -> None:
    """Test generate short uuid."""
    short_uuid = api._generate_short_uuid()
    assert len(short_uuid) == 12
    assert short_uuid.isalnum()
    uuid01 = api._generate_short_uuid()
    uuid02 = api._generate_short_uuid()
    assert uuid01 != uuid02


def test_resolve_machine_status() -> None:
    """Test resolve machine status."""
    test_cases = [
        ({"status": {"phase": "Pending"}}, "running", "succeed", False),
        ({"status": {"phase": "Running"}}, "running", "succeed", False),
        ({"status": {"phase": "Succeeded"}}, "terminated", "succeed", False),
        ({"status": {"phase": "Failed"}}, "terminated", "fail", False),
        ({"status": {"phase": "Unknown"}}, "terminated", "fail", False),
        ({"status": {"phase": "Pending"}}, "running", "succeed", True),
        ({"status": {"phase": "Running"}}, "running", "succeed", True),
        ({"status": {"phase": "Succeeded"}}, "terminated", "succeed", True),
        ({"status": {"phase": "Failed"}}, "terminated", "succeed", True),
        ({"status": {"phase": "Unknown"}}, "terminated", "succeed", True),
    ]

    for pod, expected_status, expected_result, is_return_req in test_cases:
        machine_status, machine_result = api._resolve_machine_status(
            pod, is_return_req=is_return_req
        )
        assert machine_status == expected_status
        assert machine_result == expected_result


def test_write_pod_spec() -> None:
    """Test write pod spec."""
    workdir = pathlib.Path(get_workdir())
    template_id = "Template-K8s-A"  # Exists in resources/templates.tpl
    temp_confdir = generate_provider_conf()
    templates_path = pathlib.Path(temp_confdir) / "k8sprov_templates.json"
    pod_spec = get_pod_spec()

    api._write_podspec(workdir, templates_path, template_id)

    pod_spec_file = workdir / ".podspec"
    assert pod_spec_file.exists()
    assert pod_spec_file.read_text() == pod_spec

    wrong_template_id = "Wrong-Template"

    with pytest.raises(
        ValueError,
        match=r"Template Id: .+ not found in templates file.",
    ):
        api._write_podspec(workdir, templates_path, wrong_template_id)

    pathlib.Path(templates_path).unlink()
    pod_spec_file.unlink()


# TODO: (zaidn) Add tests for error cases.
@mock.patch("hostfactory.events.post_events", return_value=None)
@mock.patch("hostfactory.api.tempfile.mkdtemp", return_value="/path/to/workdir/tempdir")
@mock.patch("hostfactory.api._generate_short_uuid", return_value="mock_request_id")
@mock.patch("hostfactory.api.hostfactory.atomic_symlink")
@mock.patch("pathlib.Path.rename")
@mock.patch("hostfactory.api._get_templates", return_value=mock_hf_template)
@mock.patch("pathlib.Path.write_text")
def test_request_machines(  # noqa: PLR0913
    mock_path_write_text,
    mock_get_template_file,
    mock_rename,
    mock_atomic_symlink,
    mock_generate_short_uuid,
    mock_mkdtemp,
    _mock_post_events,
) -> None:
    """Test request machines."""
    workdir = "/path/to/workdir"
    count = 3
    template_id = "mock_template_id"
    templates = "/path/to/templates.json"

    response = api.request_machines(workdir, templates, template_id, count)
    mock_generate_short_uuid.assert_called_once()
    mock_get_template_file.assert_called_once_with(pathlib.Path(templates))
    tempdir = pathlib.Path("/path/to/workdir/tempdir")
    requestdir = pathlib.Path("/path/to/workdir/requests/mock_request_id")

    mock_path_write_text.assert_called_once()
    mock_path_write_text.assert_called_with("pod-spec.yaml")
    mock_rename.assert_called_once_with(requestdir)
    mock_mkdtemp.assert_called_once()

    request_id = response["requestId"]
    assert request_id == "mock_request_id"

    assert mock_atomic_symlink.call_count == 3
    expected_symlink_calls = [
        mock.call("pending", tempdir / "mock_request_id-0"),
        mock.call("pending", tempdir / "mock_request_id-1"),
        mock.call("pending", tempdir / "mock_request_id-2"),
    ]
    mock_atomic_symlink.assert_has_calls(expected_symlink_calls)


@mock.patch("hostfactory.events.post_events", return_value=None)
@mock.patch("hostfactory.api.tempfile.mkdtemp", return_value="/path/to/workdir/tempdir")
@mock.patch("hostfactory.api._generate_short_uuid", return_value="mock_request_id")
@mock.patch("hostfactory.api.hostfactory.atomic_symlink")
@mock.patch("pathlib.Path.rename")
@mock.patch("pathlib.Path.exists", return_value=True)
def test_request_return_machines(
    _mock_pathlib_exists,
    mock_rename,
    mock_atomic_symlink,
    mock_generate_short_uuid,
    mock_mkdtemp,
    _mock_post_events,
) -> None:
    """Test request return machines."""
    workdir = "/path/to/workdir"
    machines = [
        {"machineId": "uuid-0", "name": "machine-0"},
        {"machineId": "uuid-1", "name": "machine-1"},
        {"machineId": "uuid-2", "name": "machine-2"},
    ]

    response = api.request_return_machines(workdir, machines)

    mock_generate_short_uuid.assert_called_once()
    tempdir = pathlib.Path("/path/to/workdir/tempdir")
    returnsdir = pathlib.Path("/path/to/workdir/return-requests/mock_request_id")
    podsdir = pathlib.Path("/path/to/workdir/pods")

    mock_rename.assert_called_once_with(returnsdir)
    mock_mkdtemp.assert_called_once()

    request_id = response["requestId"]
    assert request_id == "mock_request_id"
    assert mock_atomic_symlink.call_count == 3
    expected_symlink_calls = [
        mock.call(podsdir / "machine-0", tempdir / "mock_request_id-0"),
        mock.call(podsdir / "machine-1", tempdir / "mock_request_id-1"),
        mock.call(podsdir / "machine-2", tempdir / "mock_request_id-2"),
    ]
    mock_atomic_symlink.assert_has_calls(expected_symlink_calls)


@mock.patch("hostfactory.events.post_events", return_value=None)
@mock.patch("hostfactory.api.pathlib.Path.exists")
@mock.patch("hostfactory.api.pathlib.Path.iterdir")
@mock.patch(
    "hostfactory.api.pathlib.Path.open",
    new_callable=mock.mock_open,
    read_data=json.dumps(mock_pod),
)
@mock.patch("hostfactory.api._resolve_machine_status")
def test_get_request_status(
    mock_resolve_machine_status,
    mock_open,
    mock_iterdir,
    mock_exists,
    _mock_post_events,
) -> None:
    """Test get request status."""
    workdir = "/path/to/workdir"
    hf_req_ids = ["req1"]

    mock_exists.return_value = True
    mock_iterdir.return_value = [pathlib.Path("machine1")]
    mock_resolve_machine_status.return_value = ("running", "succeed")
    # TODO: (zaidn) Add more cases to test different statuses.
    expected_response = {
        "requests": [
            {
                "requestId": "req1",
                "message": "",
                "status": "complete",
                "machines": [
                    {
                        "machineId": "uid1",
                        "name": "pod1",
                        "result": "succeed",
                        "status": "running",
                        "privateIpAddress": "192.168.1.1",
                        "publicIpAddress": "",
                        "launchtime": "1739212317",
                        "message": "Allocated by K8s hostfactory - ns: n1",
                    }
                ],
            }
        ]
    }

    # Call the function
    response = api.get_request_status(workdir, hf_req_ids)

    # Assertions
    assert response == expected_response
    mock_exists.assert_called()
    mock_iterdir.assert_called()
    mock_open.assert_called()
    mock_resolve_machine_status.assert_called()


@mock.patch("hostfactory.api.pathlib.Path.exists")
@mock.patch("hostfactory.api.pathlib.Path.iterdir")
@mock.patch("hostfactory.api.pathlib.Path.open", new_callable=mock.mock_open)
def test_get_return_requests(mock_open, mock_iterdir, mock_exists) -> None:
    """Test get_return_requests."""
    machines = [
        {"machineId": "machine-0", "name": "pod1"},
        {"machineId": "machine-1", "name": "pod2"},
        {"machineId": "machine-2", "name": "pod3"},
    ]

    mock_exists.return_value = True
    mock_iterdir.return_value = [
        pathlib.Path("file1"),
        pathlib.Path("file2"),
        pathlib.Path("file3"),
    ]
    mock_open.side_effect = [
        mock.mock_open(
            read_data=json.dumps(
                {"metadata": {"name": "pod1"}, "status": {"phase": "Running"}}
            )
        ).return_value,
        mock.mock_open(
            read_data=json.dumps(
                {"metadata": {"name": "pod2"}, "status": {"phase": "Running"}}
            )
        ).return_value,
        mock.mock_open(
            read_data=json.dumps(
                {"metadata": {"name": "pod4"}, "status": {"phase": "Running"}}
            )
        ).return_value,
    ]

    response = api.get_return_requests("/path/to/workdir", machines)
    extra = {request["machine"] for request in response["requests"]}
    assert extra == {"pod3"}
