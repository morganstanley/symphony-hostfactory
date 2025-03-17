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

import base64
import logging
import os
import pathlib
import sqlite3
from typing import Callable

import inotify.adapters

from hostfactory.cli import context

logger = logging.getLogger(__name__)


def init_events_db() -> None:
    """Initialize database."""
    dbfile = context.GLOBAL.dbfile

    if dbfile is None:
        raise ValueError("Database file path is not provided.")

    logger.info("Initialize database: %s", dbfile)
    conn = sqlite3.connect(dbfile)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pods (
            pod TEXT PRIMARY KEY,
            request TEXT,
            return_request TEXT,
            node TEXT,
            template_id TEXT,
            requested INTEGER,
            returned INTEGER,
            created INTEGER,
            deleted INTEGER,
            scheduled INTEGER,
            pending INTEGER,
            running INTEGER,
            succeeded INTEGER,
            failed INTEGER,
            disrupted INTEGER,
            disrupted_reason TEXT,
            disrupted_message TEXT,
            container_statuses TEXT,
            unknown INTEGER,
            ready INTEGER,
            cpu_requested REAL,
            cpu_limit REAL,
            memory_requested REAL,
            memory_limit REAL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS nodes (
            node TEXT,
            uid TEXT,
            node_size TEXT,
            capacity_type TEXT,
            aws_zone TEXT,
            aws_region TEXT,
            created INTEGER,
            deleted INTEGER,
            ready INTEGER,
            conditions TEXT,
            cpu_capacity REAL,
            cpu_allocatable REAL,
            memory_capacity REAL,
            memory_allocatable REAL,
            cpu_reserved REAL,
            memory_reserved REAL,
            PRIMARY KEY (node, uid)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            request_id TEXT PRIMARY KEY,
            is_return_req INT,
            begin_time INT,
            end_time INT,
            status TEXT,
            count INT,
            template_id TEXT
        )
        """
    )

    conn.commit()
    context.GLOBAL.conn = conn


def event_average(workdir, event_from, event_to):
    """Returns the average time between two events given a connection"""
    dbfile = pathlib.Path(workdir) / "events.db"

    dirname = pathlib.Path(workdir) / "events"
    dirname.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(dbfile)
    cursor = conn.cursor()

    cursor.execute(
        f"""
        SELECT AVG({event_to} - {event_from}) AS avg_time_seconds
        FROM pods
        """  # noqa: S608
    )
    return cursor.fetchone()[0]


def _get_event_data_from_file(filename: str) -> str:
    """Returns the event data from a file"""
    with pathlib.Path(filename).open("r", encoding="utf-8") as file:
        return file.read()


def _execute_sql(cursor, sql: str, params: tuple) -> None:
    """Execute SQL statement."""
    cursor.execute(sql, params)


def _process_pod_event(cursor, ev_id, ev_key, ev_value) -> None:
    """Process pod event."""
    logger.info("Upsert pod: %s %s %s", ev_id, ev_key, ev_value)

    if ev_key in ["disrupted_message", "container_statuses"]:
        filename = base64.b64decode(ev_value).decode("utf-8")
        ev_value = _get_event_data_from_file(filename)
        logger.info("Event value from file %s: %s", filename, ev_value)

    sql = f"""
    INSERT INTO pods (pod, {ev_key}) VALUES (?, ?)
    ON CONFLICT(pod)
    DO UPDATE SET {ev_key} = ? WHERE pod = ?
    """  # noqa: S608
    params = (ev_id, ev_value, ev_value, ev_id)
    if ev_key not in ["container_statuses"]:
        sql += f" AND {ev_key} IS NULL"
    _execute_sql(cursor, sql, params)


def _process_node_event(cursor, ev_id, ev_key, ev_value) -> None:
    """Process node event."""
    node, uid = ev_id.split("::")
    logger.info("Upsert node: %s %s %s %s", node, uid, ev_key, ev_value)

    if ev_key in ["conditions"]:
        filename = base64.b64decode(ev_value).decode("utf-8")
        ev_value = _get_event_data_from_file(filename)
        logger.info("Event value from file %s: %s", filename, ev_value)

    sql = f"""
    INSERT INTO nodes (node, uid, {ev_key}) VALUES (?, ?, ?)
    ON CONFLICT(node, uid)
    DO UPDATE SET {ev_key} = ? WHERE node = ? AND uid = ?
    """  # noqa: S608
    params = (node, uid, ev_value, ev_value, node, uid)
    if ev_key not in ["conditions"]:
        sql += f" AND {ev_key} IS NULL"
    _execute_sql(cursor, sql, params)


def _get_request_event_handler(is_return_req: int) -> Callable:
    """Get request event handler."""

    def _process_request_event(cursor, ev_id, ev_key, ev_value) -> None:
        """Process request event."""
        logger.info("Upsert request: %s %s %s", ev_id, ev_key, ev_value)

        sql = f"""
        INSERT INTO requests (request_id, is_return_req, {ev_key})
        VALUES (?, ?, ?)
        ON CONFLICT(request_id)
        DO UPDATE SET {ev_key} = ? WHERE request_id = ?
        """  # noqa: S608
        params = (ev_id, is_return_req, ev_value, ev_value, ev_id)
        _execute_sql(cursor, sql, params)

    return _process_request_event


def _process_events(path, conn, files) -> None:
    """Process events.

    Events are processed in a single SQLite transaction. Once transaction
    completes, all events are deleted from the directory.
    """
    cursor = conn.cursor()

    handlers = {
        "pod": _process_pod_event,
        "node": _process_node_event,
        "request": _get_request_event_handler(0),
        "return": _get_request_event_handler(1),
    }

    for filename in files:
        logger.info("Processing event: %s/%s", path, filename)
        ev_type, ev_id, ev_key, ev_value = filename.split("~")

        if not handlers.get(ev_type):
            logger.error("Unknown event type: %s", ev_type)
            continue

        handler: Callable = handlers.get(ev_type)

        if ev_value == "None":
            ev_value = None

        handler(cursor, ev_id, ev_key, ev_value)

    conn.commit()

    for filename in files:
        os.unlink(os.path.join(path, filename))  # noqa: PTH108, PTH118


def process_events(watch=True) -> None:
    """Process events."""
    logger.info("Processing events: %s", context.GLOBAL.dirname)

    conn = context.GLOBAL.conn

    _process_events(
        context.GLOBAL.dirname,
        conn,
        [
            filename
            for filename in os.listdir(str(context.GLOBAL.dirname))
            if not filename.startswith(".")
        ],
    )

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
        _process_events(
            path,
            conn,
            [filename for filename in os.listdir(path) if not filename.startswith(".")],
        )


def post_events(events) -> None:
    """Post events. "events" is a list of tuples, each tuple is an event."""
    for event in events:
        ev_type, ev_id, ev_key, ev_value = event
        pathlib.Path(context.GLOBAL.dirname).joinpath(
            "~".join([ev_type, ev_id, ev_key, str(ev_value)])
        ).touch()
