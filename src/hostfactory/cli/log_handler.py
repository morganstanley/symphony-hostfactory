"""Morgan Stanley makes this available to you under the Apache License, Version 2.0
(the "License"). You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0. See the NOTICE file distributed
with this work for additional information regarding copyright ownership.
Unless required by applicable law or agreed to in writing, software distributed
 under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
 CONDITIONS OF ANY KIND, either express or implied.  See the License for the
 specific language governing permissions and limitations under the License.

Logging setup.
"""

import datetime
import logging
import sys
import tempfile

import wrapt
from rich.console import Console
from rich.text import Text

from hostfactory.cli import context

SUPPORT_UNICODE = True
MAX_PANEL_WIDTH = 100
LOG_FORMAT = (
    "%(asctime)s %(levelname)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s"
)
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# This is where we hold the stderr Console object
# once it is initialised by setup_logging()
CONSOLE = wrapt.ObjectProxy(None)


class MarkupHandler(logging.Handler):
    """A log handler that is a tad bit prettier than stock,
    and optionally applies markup to the message body itself.
    NB: rich provides a built-in log handler, but that behaves a little OTT
        and, somewhat ironically, breaks live renderables.
    """

    def __init__(
        self,
        markup_in_message,
        show_timestamp=None,
        show_level=True,
        show_logger=False,
    ) -> None:
        """Initialize the log handler."""
        super().__init__()
        self.markup_in_message = markup_in_message
        self.show_timestamp = show_timestamp
        self.show_level = show_level
        self.show_logger = show_logger

    def emit(self, record):
        """Emit the log record."""
        renderables = []
        if self.show_timestamp:
            timestamp = datetime.datetime.fromtimestamp(record.created)
            renderables.append(
                Text.styled(timestamp.isoformat(sep=" ")[:23], style="log.time")
            )

        if self.show_logger:
            renderables.append(Text(record.name, style="bright_black"))

        if self.show_level:
            level = record.levelname
            renderables.append(
                Text.styled(level, style=f"logging.level.{level.lower()}")
            )

        # Debug (and lower) logging does not need to be prettified,
        # and in fact routinely contains markdown-breaking stuff like
        # JSON objects and arrays, so let's not bother.
        render_markup = self.markup_in_message and record.levelno > logging.DEBUG

        message = self.format(record)
        renderables.append(
            Text.from_markup(message, emoji=False) if render_markup else Text(message)
        )

        CONSOLE.print(*renderables, soft_wrap=True)


def setup_logging(log_level: str) -> None:
    """Setup logging handlers. Invoke once."""
    # ruff: noqa: PLW0603
    # We can live with the global constant mutation, because upstream
    # components need to know whether the console supports unicode or not.

    global SUPPORT_UNICODE
    debug = log_level == "debug"

    if not sys.stderr.encoding.lower().startswith("utf"):
        SUPPORT_UNICODE = False

    CONSOLE.__wrapped__ = Console(stderr=True, emoji=SUPPORT_UNICODE)

    plain_handler = MarkupHandler(markup_in_message=False, show_logger=debug)
    plain_handler.setLevel(logging.DEBUG if debug else logging.INFO)

    with tempfile.NamedTemporaryFile(delete=False, mode="w") as lf:
        context.GLOBAL.logfile = lf.name

    file_handler = logging.FileHandler(filename=context.GLOBAL.logfile)
    file_formatter = logging.Formatter(LOG_FORMAT)
    file_handler.setFormatter(file_formatter)
    # Root logger is conservative. No markup support because
    # we can't expect libraries not to try to emit broken markdown.
    logging.basicConfig(
        format="%(message)s",
        level=LOG_LEVELS[log_level.upper()],
        handlers=[plain_handler],
    )

    # For the loggers we *do* control, support full markup.
    logger = logging.getLogger("hostfactory")
    logger.addHandler(file_handler)

    logger.propagate = False
