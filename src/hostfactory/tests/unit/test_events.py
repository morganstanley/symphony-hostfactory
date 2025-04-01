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

Test processing of events.
"""

import json
import pathlib
import tempfile
from contextlib import closing

from hostfactory import events
from hostfactory.cli import context


def test_post_event() -> None:
    """Test pod events in directory"""
    with tempfile.TemporaryDirectory() as dirname:
        context.GLOBAL.dirname = dirname

        events.post_event(
            category="pod",
            id="abcd-0",
            request="abcd",
        )

        found = False
        for eventfile in pathlib.Path(dirname).iterdir():
            if eventfile.name[0] == ".":
                continue
            payload = json.loads(eventfile.read_text())
            assert isinstance(payload, list | tuple)
            assert len(payload) == 1
            event = payload[0]
            assert event["category"] == "pod"
            assert event["id"] == "abcd-0"
            assert event["request"] == "abcd"
            found = True
        assert found


def test_sqlite_events_backend() -> None:
    """Test pod events with sqlite."""
    backend = events.SqliteEventBackend(":memory:")
    backend.post(
        {
            "category": "pod",
            "id": "abcd-0",
            "request": "abcd",
        }
    )

    with closing(backend.conn.cursor()) as cur:
        cur.execute("SELECT category, id, type, value FROM events")
        result = cur.fetchone()
        assert result == (
            "pod",
            "abcd-0",
            "request",
            "abcd",
        )

    backend.post(
        {
            "category": "pod",
            "id": "abcd-0",
            "pending": 10001,
        }
    )

    with closing(backend.conn.cursor()) as cur:
        cur.execute("SELECT category, id, type, value FROM events WHERE type='pending'")
        result = cur.fetchone()
        assert result == (
            "pod",
            "abcd-0",
            "pending",
            "10001",
        )
