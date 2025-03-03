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

import tempfile
from contextlib import closing

from hostfactory import events
from hostfactory.cli import context


def test_pod_events() -> None:
    """Test pod events."""
    with tempfile.TemporaryDirectory() as dirname:
        context.GLOBAL.dbfile = ":memory:"
        context.GLOBAL.dirname = dirname
        events.init_events_db()

        events.post_events([("pod", "abcd-0", "request", "abcd")])
        events.process_events(watch=False)

        with closing(context.GLOBAL.conn.cursor()) as cur:
            cur.execute("SELECT request, pending FROM pods")
            result = cur.fetchone()
            assert result == (
                "abcd",
                None,
            )

        events.post_events([("pod", "abcd-0", "pending", 10001)])
        events.process_events(watch=False)

        with closing(context.GLOBAL.conn.cursor()) as cur:
            cur.execute("SELECT request, pending FROM pods")
            result = cur.fetchone()
            assert result == (
                "abcd",
                10001,
            )
