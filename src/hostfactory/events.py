"""Morgan Stanley makes this available to you under the Apache License, Version 2.0
(the "License"). You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0. See the NOTICE file distributed
with this work for additional information regarding copyright ownership.
Unless required by applicable law or agreed to in writing, software distributed
 under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
 CONDITIONS OF ANY KIND, either express or implied.  See the License for the
 specific language governing permissions and limitations under the License.

Process and collect hostfactory events.

This module will collect pod and nodes events and store them in a SQLite
database.

TODO: Should we consider jaeger/open-telemetry for tracing? Probably, but the
      immediate goal is to collect and store stats about requests and to be
      able to compare them with subsequent runs.
"""

import logging
import os
import pathlib
import sqlite3

import inotify.adapters

from hostfactory.cli import context


def init_events_db() -> None:
    """Initialize database."""
    dbfile = context.dbfile

    logging.info("Initialize database: %s", dbfile)  # noqa: TID251
    conn = sqlite3.connect(dbfile)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pods (
            pod TEXT PRIMARY KEY,
            request TEXT,
            return_request TEXT,
            node TEXT,
            requested INTEGER,
            returned INTEGER,
            created INTEGER,
            deleted INTEGER,
            scheduled INTEGER,
            pending INTEGER,
            running INTEGER,
            succeeded INTEGER,
            failed INTEGER,
            unknown INTEGER,
            ready INTEGER
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS nodes (
            node TEXT,
            uid TEXT,
            created INTEGER,
            deleted INTEGER,
            ready INTEGER,
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
            status TEXT
        )
        """
    )

    conn.commit()
    context.conn = conn


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


def _process_events(path, conn, files) -> None:
    """Process events.

    Events are processed in a single SQLite transaction. Once transaction
    completes, all events are deleted from the directory.
    """
    cursor = conn.cursor()

    def _pod_event(cursor, ev_id, ev_key, ev_value) -> None:
        """Process pod event."""
        logging.info("Upsert pod: %s %s %s", ev_id, ev_key, ev_value)  # noqa: TID251
        cursor.execute(
            f"""
            INSERT INTO pods (pod, {ev_key}) VALUES (?, ?)
            ON CONFLICT(pod)
            DO UPDATE SET {ev_key} = ? WHERE pod = ? AND {ev_key} IS NULL
            """,  # noqa: S608
            (ev_id, ev_value, ev_value, ev_id),
        )

    def _node_event(cursor, ev_id, ev_key, ev_value) -> None:
        """Process node event."""
        node, uid = ev_id.split("::")
        logging.info("Upsert node: %s %s %s %s", node, uid, ev_key, ev_value)  # noqa: TID251
        cursor.execute(
            f"""
            INSERT INTO nodes (node, uid, {ev_key}) VALUES (?, ?, ?)
            ON CONFLICT(node, uid)
            DO UPDATE SET {ev_key} = ?
            WHERE node = ? AND uid = ? AND {ev_key} IS NULL
            """,  # noqa: S608
            (node, uid, ev_value, ev_value, node, uid),
        )

    def _request_event(cursor, ev_id, ev_key, ev_value) -> None:
        """Process request event."""
        logging.info("Upsert request: %s %s %s", ev_id, ev_key, ev_value)  # noqa: TID251
        cursor.execute(
            f"""
            INSERT INTO requests (request_id, is_return_req, {ev_key})
            VALUES (?, ?, ?)
            ON CONFLICT(request_id)
            DO UPDATE SET {ev_key} = ? WHERE request_id = ?
            """,  # noqa: S608
            (ev_id, 0, ev_value, ev_value, ev_id),
        )

    def _return_event(cursor, ev_id, ev_key, ev_value) -> None:
        """Process return request event."""
        logging.info("Upsert return request: %s %s %s", ev_id, ev_key, ev_value)  # noqa: TID251
        cursor.execute(
            f"""
            INSERT INTO requests (request_id, is_return_req, {ev_key})
            VALUES (?, ?, ?)
            ON CONFLICT(request_id)
            DO UPDATE SET {ev_key} = ? WHERE request_id = ?
            """,  # noqa: S608
            (ev_id, 1, ev_value, ev_value, ev_id),
        )

    handlers = {
        "pod": _pod_event,
        "node": _node_event,
        "request": _request_event,
        "return": _return_event,
    }

    for filename in files:
        logging.info("Processing event: %s/%s", path, filename)  # noqa: TID251
        ev_type, ev_id, ev_key, ev_value = filename.split("~")

        handler = handlers.get(ev_type)
        if not handler:
            logging.error("Unknown event type: %s", ev_type)  # noqa: TID251
            continue

        handler(cursor, ev_id, ev_key, ev_value)

    conn.commit()

    for filename in files:
        os.unlink(os.path.join(path, filename))  # noqa: PTH108, PTH118


def process_events(watch=True) -> None:
    """Process events."""
    logging.info("Processing events: %s", context.dirname)  # noqa: TID251

    conn = context.conn

    _process_events(
        context.dirname,
        conn,
        [
            filename
            for filename in os.listdir(str(context.dirname))
            if not filename.startswith(".")
        ],
    )

    if not watch:
        return

    dirwatch = inotify.adapters.Inotify()

    # Add the path to watch
    dirwatch.add_watch(
        str(context.dirname),
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
        pathlib.Path(context.dirname).joinpath(
            "~".join([ev_type, ev_id, ev_key, str(ev_value)])
        ).touch()
