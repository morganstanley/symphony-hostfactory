"""Test processing of events."""

import tempfile
from contextlib import closing

from hostfactory import events
from hostfactory.cli import context


def test_pod_events() -> None:
    """Test pod events."""
    with tempfile.TemporaryDirectory() as dirname:
        context.dbfile = ":memory:"
        context.dirname = dirname
        events.init_events_db()

        events.post_events([("pod", "abcd-0", "request", "abcd")])
        events.process_events(watch=False)

        with closing(context.conn.cursor()) as cur:
            cur.execute("SELECT request, pending FROM pods")
            result = cur.fetchone()
            assert result == (  # noqa: S101
                "abcd",
                None,
            )

        events.post_events([("pod", "abcd-0", "pending", 10001)])
        events.process_events(watch=False)

        with closing(context.conn.cursor()) as cur:
            cur.execute("SELECT request, pending FROM pods")
            result = cur.fetchone()
            assert result == (  # noqa: S101
                "abcd",
                10001,
            )
