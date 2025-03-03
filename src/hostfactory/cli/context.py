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

Captures common cli options, allows to retrieve them later
"""


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
        self.templates = None

    @property
    def default_workdir(self) -> str:
        """Get the default workdir."""
        return self.workdir if self.workdir else "/tmp/hostfactory"  # noqa: S108


GLOBAL = Context()
