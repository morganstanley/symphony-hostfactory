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

Common utilities.
"""

import functools
import json
import logging
import pathlib
import random
import string
import sys
import tempfile
import traceback
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from typing import Callable
from typing import Tuple
from typing import TypeVar

import click

_DecoratedFuncT = TypeVar("_DecoratedFuncT", bound=Callable[..., Any])
type _ExceptionHandler = Tuple[type[Exception], None | str | Callable[[Exception], str]]

logger = logging.getLogger(__name__)
EXIT_CODE_DEFAULT = 1


def generate_short_uuid() -> str:
    """Generates a short UUID for hfreqid.
    Returns:
        str: A short UUID string of length 12.
    """
    alphabet = string.ascii_lowercase + string.digits
    return random.choice(string.ascii_lowercase) + "".join(
        random.choices(alphabet, k=11)
    )


def atomic_write(data, dst):
    """Atomically create a file with given content"""
    if not isinstance(data, bytes):
        if not isinstance(data, str):
            data = json.dumps(data)
        data = data.encode("utf-8")
    dst = pathlib.Path(dst)
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".",
            dir=dst.parent,
            delete=False,
        ) as tf:
            tf.write(data)
            tf.flush()
            pathlib.Path.rename(pathlib.Path(tf.name), dst)
    except OSError as exc:
        logger.exception("Exception occurred: %s", exc)
        raise RuntimeError from exc


class DateTimeEncoder(json.JSONEncoder):
    """A custom JSON encoder that extends the `json.JSONEncoder` class.
    This encoder is used to serialize `datetime` objects into ISO 8601 format.
    """

    def default(self, o):
        """Convert the given date object to a JSON-serializable format."""
        if isinstance(o, datetime):
            return int(o.timestamp())

        try:
            return o.to_dict()
        except AttributeError:
            pass

        return super().default(o)


def handle_exceptions(
    exclist: Sequence[_ExceptionHandler],
) -> Callable[[_DecoratedFuncT], _DecoratedFuncT]:
    """Decorator that will handle exceptions and output friendly messages."""

    def wrap(f):  # noqa: ANN202
        """Returns decorator that wraps/handles exceptions."""
        exclist_copy = list(exclist)

        def wrapped_f(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
            """Wrapped function."""
            if not exclist_copy:
                f(*args, **kwargs)
            else:
                exc_handler: _ExceptionHandler = exclist_copy.pop(0)
                exc, handler = exc_handler

                try:
                    wrapped_f(*args, **kwargs)
                except exc as err:
                    if isinstance(handler, str):
                        traceback.print_exc()
                        click.echo(handler, err=True)
                    elif handler is None:
                        traceback.print_exc()
                        click.echo(str(err), err=True)
                    else:
                        click.echo(handler(err), err=True)

                    sys.exit(EXIT_CODE_DEFAULT)

        @functools.wraps(f)
        def _handle_any(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            """Default exception handler."""
            try:
                return wrapped_f(*args, **kwargs)

            except click.UsageError as usage_err:
                click.echo(f"Usage error: {usage_err!s}", err=True)
                sys.exit(EXIT_CODE_DEFAULT)

            except Exception as unhandled:  # pylint: disable=W0703  # noqa: BLE001
                with tempfile.NamedTemporaryFile(
                    delete=False, mode="w", encoding="utf-8"
                ) as f:  # noqa: PLR1704
                    traceback.print_exc(file=f)
                    click.echo(
                        f"Error: {unhandled} [ {f.name} ]",
                        err=True,
                    )

                sys.exit(EXIT_CODE_DEFAULT)

        return _handle_any

    return wrap
