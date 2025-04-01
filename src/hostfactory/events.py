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

Process and collect hostfactory events.

This module will collect pod and nodes events and store them in a SQLite
database.

TODO: Should we consider jaeger/open-telemetry for tracing? Probably, but the
      immediate goal is to collect and store stats about requests and to be
      able to compare them with subsequent runs.
"""

import json
import logging
import os
import pathlib
import sqlite3
import sys
from time import time
from typing import Any
from uuid import uuid4

import inotify.adapters
from tenacity import before_sleep_log
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_exponential

from hostfactory import atomic_write
from hostfactory.cli import context

logger = logging.getLogger(__name__)


def _extract_metadata(src) -> dict[str, str]:
    metadata = {}
    prefix = "HF_K8S_METADATA_"
    for k, v in src.items():
        if k.startswith(prefix):
            suffix = k[len(prefix) :]
            if suffix:
                metadata[suffix.lower()] = v
    return metadata


EXTRA_METADATA = _extract_metadata(os.environ)


class ConsoleEventBackend:
    """Dump events to console, intended for debugging"""

    def __init__(self, use_stderr=False, indent=None) -> None:
        """Init the console backend"""
        self.fd = sys.stderr if use_stderr else sys.stdout
        self.indent = indent

    def post(self, event):
        """Post event to the console"""
        print(json.dumps(event, indent=self.indent), file=self.fd, flush=True)

    def close(self):
        """N/A"""


class SqliteEventBackend:
    """Dump events into a SQLite db"""

    def __init__(self, dbfile=None) -> None:
        """Initialize database."""
        dbfile = dbfile or context.GLOBAL.dbfile

        if dbfile is None:
            raise ValueError("Database file path is not provided.")

        logger.info("Initialize database: %s", dbfile)
        pathlib.Path(dbfile).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(dbfile)

        with self.conn as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    hf_namespace TEXT,
                    hf_pod TEXT,
                    category TEXT,
                    id TEXT,
                    timestamp INT,
                    type TEXT,
                    value TEXT
                )
                """
            )

        self.known_columns = frozenset(
            (
                "hf_namespace",
                "hf_pod",
                "category",
                "id",
                "timestamp",
            )
        )

    def post(self, event):
        """Post given event to the underlying SQLite db"""
        known = []
        rows = []
        for k, v in event.items():
            if k in self.known_columns:
                known.append((k, v))
            else:
                rows.append((k, v))

        if not rows:
            return

        names = [i[0] for i in known]
        names.append("type")
        names.append("value")
        values = [i[1] for i in known]
        values.append(None)
        values.append(None)
        assert len(names) == len(values)  # noqa: S101
        names_str = ",".join(names)
        values_str = ",".join("?" for _ in range(len(values)))
        sql = f"INSERT INTO events ({names_str}) VALUES ({values_str})"  # noqa: S608
        with self.conn as conn:
            for t, v in rows:
                values[-2] = t
                values[-1] = v
                conn.execute(sql, tuple(values))

    def close(self):
        """Close the db"""
        self.conn.close()
        self.conn = None


def _pending_events(eventdir) -> tuple[pathlib.Path]:
    return tuple(
        child
        for child in pathlib.Path(eventdir).iterdir()
        if child.is_file() and child.name[0] != "."
    )


def _process_events(backends, eventfiles) -> None:
    for eventfile in eventfiles:
        logger.info("Processing event in: %s", eventfile)
        try:
            try:
                events = json.loads(eventfile.read_text())
            except ValueError:
                continue
            if not isinstance(events, list | tuple):
                events = [events]
            for backend in backends:
                for event in events:
                    backend.post(event)
        finally:
            eventfile.unlink(missing_ok=True)


@retry(
    retry=retry_if_exception_type(sqlite3.OperationalError),
    wait=wait_exponential(),
    stop=stop_after_attempt(5),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def process_events(watch=True) -> None:
    """Process events."""
    logger.info("Processing events: %s", context.GLOBAL.dirname)
    backends = (
        ConsoleEventBackend(),
        SqliteEventBackend(),
    )

    try:
        _process_events(backends, _pending_events(context.GLOBAL.dirname))

        if not watch:
            return

        dirwatch = inotify.adapters.Inotify()

        # Add the path to watch
        dirwatch.add_watch(
            str(context.GLOBAL.dirname),
            mask=inotify.constants.IN_CREATE | inotify.constants.IN_MOVED_TO,
        )

        for event in dirwatch.event_gen(yield_nones=False):
            (_, _type_names, path, _filename) = event
            _process_events(backends, _pending_events(path))
    finally:
        for backend in backends:
            backend.close()


def post_event(*args: list[dict[str, Any]], **kwargs: dict[str, Any]) -> None:
    """Post a single event."""
    if args:
        assert len(args) == 1  # noqa: S101
        post_events(args[0])
    else:
        post_events(kwargs)


def post_events(*events: list[dict[str, Any]]) -> None:
    """Post multiple events."""
    timestamp = int(time())

    buf = []
    for event in events:
        if isinstance(event, list | tuple):
            buf.extend(event)
        else:
            buf.append(event)

    for event in buf:
        assert isinstance(event, dict)  # noqa: S101
        if "timestamp" not in event:
            event["timestamp"] = timestamp

    filename = f"{int(time() * 1000)}-{uuid4()}"
    atomic_write(
        [EXTRA_METADATA | event for event in buf],
        pathlib.Path(context.GLOBAL.dirname) / filename,
    )


class EventsBuffer:
    """Context manager for batched event push"""

    def __init__(self) -> None:
        """Prepare a fresh events buffer"""
        self.events: list[dict[str, Any]] = []

    def __enter__(self) -> "EventsBuffer":
        """Enter the context manager"""
        assert not self.events  # noqa: S101
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool | None:
        """Exit the context manager"""
        if exc_type is None and self.events:
            post_events(self.events)
            self.events = []
        return None

    def post(self, *args: list[Any], **kwargs: dict[str, Any]):
        """Buffer event(s)."""
        if args:
            assert not kwargs  # noqa: S101
            self.events.extend(args)
        else:
            assert kwargs  # noqa: S101
            self.events.append(kwargs)
