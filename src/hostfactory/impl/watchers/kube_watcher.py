"""Watches nodes events are writes them to the database"""

import json
import logging
import pathlib

from hostfactory import events as hfevents
from hostfactory import k8sutils
from hostfactory.impl.watchers import cluster

logger = logging.getLogger(__name__)


def watch(workdir: pathlib.Path) -> None:
    """Watch for kubernetes events."""
    k8sutils.watch_kube_events(
        workdir=workdir,
        _postprocess_event=_postprocess_event,
        _event_path=_event_path,
        field_selector="involvedObject.kind=Node",
        handler=cluster.handle_event,
    )


def _event_path(workdir: pathlib.Path, data: dict) -> pathlib.Path:
    """If None is returned, event should not be saved"""
    logger.debug("No event path for event %s in %s", data, workdir)
    return None


def _postprocess_event(workdir: pathlib.Path, event: dict) -> None:
    """Push kubernetes event to db."""
    logger.debug("Processing kube event %s in workdir %s", event, workdir)
    data = event["object"]

    involved_object = data.involved_object
    if involved_object.kind != "Node":
        logger.warning("Skipping event %s that is not for node.", data.metadata.name)
        return

    node_id = f"{involved_object.name}::{involved_object.uid}"

    with hfevents.EventsBuffer() as events:
        parsed_node_event = k8sutils.parse_node_event(data)
        if not parsed_node_event:
            return
        if parsed_node_event["message"] == "Disrupting Node: Underutilized/Delete":
            events.post(
                category="node",
                id=node_id,
                eviction_uderutilized=str(parsed_node_event["timestamp"]),
            )
        if parsed_node_event["message"] == "Disrupting Node: Empty/Delete":
            events.post(
                category="node",
                id=node_id,
                eviction_empty=str(parsed_node_event["timestamp"]),
            )
        events.post(
            category="node",
            id=node_id,
            events=json.dumps(parsed_node_event),
        )
