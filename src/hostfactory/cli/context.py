"""Captures common cli options, allows to retrieve them later"""


class Context:
    """Global cli context"""

    def __init__(self) -> None:
        """Initialize context."""
        self.debug = False
        self.logfile = None
        self.workdir = None
        self.dbfile = None
        self.conn = None
        self.dirname = None


GLOBAL = Context()
