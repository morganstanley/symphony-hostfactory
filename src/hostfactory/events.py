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

from hostfactory import fsutils
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

    def post(self, events):
        """Post event to the console"""
        for event in events:
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
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS events_idx ON events(
                    hf_namespace,
                    hf_pod,
                    category,
                    id,
                    timestamp,
                    type,
                    value
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

    def _prepare_event_for_db(self, event: dict[str, Any]) -> list[tuple[Any]] | None:
        """Prepare event for SQLite database."""
        # Initialize the formatted event with known columns
        known_columns_data = {col: event.get(col, "") for col in self.known_columns}

        # Find the first unknown column (SQLite schema supports one type-value pair)
        unknown_columns_data = [
            (k, v) for k, v in event.items() if k not in self.known_columns and v
        ]

        if unknown_columns_data:
            # Format the event by merging known columns with the unknown column's
            # type and value
            formatted_event_values = []
            for key, value in unknown_columns_data:
                type_value_pair = {
                    "type": key,
                    "value": (
                        value
                        if isinstance(value, int | float | str)
                        else json.dumps(value, sort_keys=True)
                    ),
                }

                # Sort the merged dictionary and get the values alone in tuple
                formatted_event_data = tuple(
                    value
                    for _, value in sorted(
                        (known_columns_data | type_value_pair).items()
                    )
                )

                formatted_event_values.append(formatted_event_data)

            return formatted_event_values

        # No unknown columns, skip this event
        return None

    def post(self, events: list[dict[str, Any]]) -> None:
        """Post given events to the underlying SQLite database."""
        if not events:
            return

        db_events = []
        for event in events:
            formatted_event = self._prepare_event_for_db(event)
            if formatted_event:
                db_events.extend(formatted_event)

        # Prepare SQL query components
        sorted_columns = sorted(self.known_columns | {"type", "value"})
        columns_str = ", ".join(sorted_columns)
        placeholders = ", ".join("?" for _ in sorted_columns)

        # Execute the query in a single transaction
        with self.conn as conn:
            conn.executemany(
                f"""
                INSERT INTO events ({columns_str}) VALUES ({placeholders})
                ON CONFLICT DO NOTHING
                """,  # noqa: S608
                db_events,
            )
        logger.info("Inserted events into the database: %s", db_events)

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
    """Process events from the given list of event files."""
    all_events = []
    for eventfile in eventfiles:
        logger.info("Processing event in: %s", eventfile)
        try:
            events = json.loads(eventfile.read_text())
            if not isinstance(events, list | tuple):
                events = [events]
            all_events.extend(events)
        except ValueError:
            logger.warning("Invalid JSON in file: %s", eventfile)
        finally:
            eventfile.unlink(missing_ok=True)

    for backend in backends:
        backend.post(all_events)


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


# TODO(andreik): what exactly is it batching? From the code, it looks like
# it accumulates events in the internal list, and then dumps them all into
# file system on exit. How is it different from creating files at the moment
# of event generation?
#
# The only potential reason to batch events is to generate single transaction
# in sqlite - as right now, every even is inserted as part of transaction
# and it is "potentially" slow. Remains to be seen if this is the case... but
# it is suboptimal as far as classic SQLite usage is concerned.
class EventsBuffer:
    """Context manager for batched event push"""

    def __init__(self) -> None:
        """Prepare a fresh events buffer"""
        self.events: list[dict[str, Any]] = []

    def _flush(self) -> None:
        if self.events:
            filename = f"{uuid4()}"
            fsutils.atomic_write(
                [EXTRA_METADATA | event for event in self.events],
                pathlib.Path(context.GLOBAL.dirname) / filename,
            )
            self.events = []

    def _buffer(self, *events: list[dict[str, Any]]) -> None:
        timestamp = int(time())
        for event in events:
            assert isinstance(event, dict)  # noqa: S101
            if event.get("timestamp") is None:
                event["timestamp"] = timestamp
            self.events.append(event)

    def __enter__(self) -> "EventsBuffer":
        """Enter the context manager"""
        assert not self.events  # noqa: S101
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool | None:
        """Exit the context manager"""
        self._flush()
        return None

    def post(self, *args: list[Any], **kwargs: dict[str, Any]):
        """Buffer event(s)."""
        if args:
            assert not kwargs  # noqa: S101
            self._buffer(*args)
        else:
            assert kwargs  # noqa: S101
            self._buffer(kwargs)
