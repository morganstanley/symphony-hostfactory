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

Test Hostfactory Watchers implementation.
"""

import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

import inotify.adapters
import inotify.constants

import hostfactory.watcher
from hostfactory.tests import get_pod_spec
from hostfactory.tests import get_workdir


def _setup_inotify(watchdir) -> inotify.adapters.Inotify():
    """Setup inotify object"""
    inotify_inst = inotify.adapters.Inotify()
    inotify_inst.add_watch(
        str(watchdir),
        mask=inotify.constants.IN_CREATE | inotify.constants.IN_MOVED_TO,
    )
    return inotify_inst


def _read_all_events(inotify_inst) -> list:
    """Read all events from the inotify object"""
    return list(inotify_inst.event_gen(timeout_s=1, yield_nones=False))


@mock.patch(
    "hostfactory.watcher.inotify.adapters.Inotify", return_value=mock.MagicMock()
)
@mock.patch("hostfactory.watcher._create_pod")
class TestRequestMachinesWatcher(unittest.TestCase):
    """Validate Hostfactory request machines watcher"""

    def setUp(self) -> None:
        """Setup the test"""
        self.workdir = pathlib.Path(get_workdir())
        req_id = "test-request-id"
        requests = self.workdir / "requests"
        self.req_dir = requests / req_id
        self.req_dir.mkdir(parents=True, exist_ok=True)
        self.inotify_inst = _setup_inotify(requests)

    def tearDown(self) -> None:
        """Cleanup the test"""
        del self.inotify_inst
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_request_machines_watcher(self, mock_create_pod, mock_inotify) -> None:
        """Test request machines watcher"""
        temp_dir = pathlib.Path(tempfile.mkdtemp(dir="/tmp"))

        for i in range(1, 4):
            file_name = f"machine{i}"
            temp_file_path = temp_dir / file_name
            hostfactory.atomic_symlink("pending", temp_file_path)
        pod_spec = temp_dir / ".podspec"
        pod_spec_path = get_pod_spec()
        pod_spec.write_text(pod_spec_path)

        temp_dir.rename(self.req_dir)

        mock_inotify.event_gen.return_value = _read_all_events(self.inotify_inst)

        hostfactory.watcher.watch_requests(self.workdir)

        assert mock_create_pod.call_count == 3
        calls = [
            mock.call(
                self.req_dir / f"machine{i}",
                self.req_dir.parent,
                pathlib.Path(pod_spec_path),
            )
            for i in range(1, 4)
        ]
        mock_create_pod.assert_has_calls(calls)
        assert pathlib.Path(self.req_dir / ".processed").exists()


@mock.patch(
    "hostfactory.watcher.inotify.adapters.Inotify", return_value=mock.MagicMock()
)
@mock.patch("hostfactory.watcher._delete_pod")
class TestRequestReturnMachinesWatcher(unittest.TestCase):
    """Validate Hostfactory request return machines watcher"""

    def setUp(self) -> None:
        """Setup the test"""
        self.workdir = pathlib.Path(get_workdir())
        req_id = "test-request-id"
        requests = self.workdir / "return-requests"
        self.req_dir = requests / req_id
        self.req_dir.mkdir(parents=True, exist_ok=True)
        self.inotify_inst = _setup_inotify(requests)

    def tearDown(self) -> None:
        """Cleanup the test"""
        del self.inotify_inst
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_request_return_machines_watcher(
        self, mock_delete_pod, mock_inotify
    ) -> None:
        """Test request return machines watcher"""
        mock_pod = {"metadata": {"name": "machine1"}, "status": {"phase": "Running"}}
        temp_dir = pathlib.Path(tempfile.mkdtemp(dir="/tmp"))
        for i in range(1, 4):
            file_name = f"machine{i}"
            temp_file_path = temp_dir / file_name
            mock_pod["metadata"]["name"] = file_name
            json.dump(mock_pod, temp_file_path.open("w"))
        temp_dir.rename(self.req_dir)

        mock_inotify.event_gen.return_value = _read_all_events(self.inotify_inst)

        hostfactory.watcher.watch_return_requests(self.workdir)

        assert mock_delete_pod.call_count == 3
        calls = [mock.call(f"machine{i}") for i in range(1, 4)]
        mock_delete_pod.assert_has_calls(calls)

        assert pathlib.Path(self.req_dir / ".processed").exists()
