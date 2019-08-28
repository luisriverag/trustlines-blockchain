import logging
import os

import falcon
import pkg_resources
from gevent.pywsgi import WSGIServer

from bridge.service import Service

logger = logging.getLogger(__name__)

welcome_page = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tlbc-bridge</title>
</head>

<body>
<header>
<h1>Welcome to tlbc-bridge</h1>
</header>

<p>
You have reached tlbc-bridge's REST server. This is only meant for
debugging.
</p>

<p>
If you see this in a production setup, please remove the
<code>[webservice]</code> section from your config file.
</p>

</body>
</html>
"""


class WelcomePage:
    def on_get(self, req, resp):
        resp.body = welcome_page
        resp.content_type = "text/html"


class InternalState:
    def __init__(self, *, recorder, public_config):
        self.recorder = recorder
        self.public_config = public_config

    def on_get(self, req, resp):
        resp.media = {
            "bridge": {
                "version": pkg_resources.get_distribution("tlbc-bridge").version,
                "config": self.public_config,
                "process": {
                    "pid": os.getpid(),
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                    "loadavg": os.getloadavg(),
                },
                "recorder": self.recorder.get_state_summary(),
            }
        }


class Webservice:
    def __init__(self, *, host, port):
        self.host = host
        self.port = port

        self.app = falcon.API()
        self.app.add_route("/", WelcomePage())
        self.services = [Service("webservice", self.run)]

    def enable_internal_state(self, internal_state):
        self.app.add_route("/bridge/internal-state", internal_state)

    def run(self):
        http_server = WSGIServer((self.host, self.port), self.app, log=logger)
        logger.info(f"Webservice is running on http://{self.host}:{self.port}".format())
        http_server.serve_forever()


class DummyWebservice(Webservice):
    def __init__(self, *, host, port):
        super()(host=host, port=port)
        self.services = []
