#!/usr/bin/env python3
"""Loopback-only explorer server with narrowly bounded local actions."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from explorer import Explorer
from source import InvalidSelection, Unsupported
from terminal import MacTerminalLauncher

STATIC = Path(__file__).parent / 'static'


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, explorer, terminal_launcher=None, **kwargs):
        self.explorer = explorer
        self.terminal_launcher = terminal_launcher or MacTerminalLauncher()
        super().__init__(*args, **kwargs)

    def send(self, status, body, content_type):
        payload = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        try:
            if not self.loopback_host():
                self.send(403, 'Use the loopback URL printed by the launcher.', 'text/plain; charset=utf-8')
                return
            url = urlsplit(self.path)
            query = {key: values[0] for key, values in parse_qs(url.query, keep_blank_values=True).items()}
            if url.path == '/api/state':
                result = self.explorer.snapshot()
                result['terminal_handoff'] = self.terminal_launcher.available
            elif url.path == '/api/resolve':
                result = self.explorer.resolve(query)
            elif url.path == '/api/check':
                result = self.explorer.check(query)
            elif url.path == '/source':
                path = self.explorer.source_path(query.get('path',''))
                self.send(200, path.read_bytes(), 'text/plain; charset=utf-8')
                return
            elif url.path in {'/', '/app.js', '/style.css', '/favicon.svg'}:
                name = 'index.html' if url.path == '/' else url.path[1:]
                content_type = {'index.html':'text/html', 'app.js':'text/javascript', 'style.css':'text/css', 'favicon.svg':'image/svg+xml'}[name]
                self.send(200, (STATIC/name).read_bytes(), content_type+'; charset=utf-8')
                return
            else:
                self.send(404, 'Not found.', 'text/plain; charset=utf-8')
                return
            self.send(200, json.dumps(result), 'application/json; charset=utf-8')
        except (InvalidSelection, Unsupported, ValueError, OSError, SyntaxError, TypeError, AttributeError) as error:
            self.send(422, json.dumps(dict(error=str(error))), 'application/json; charset=utf-8')

    def do_POST(self):
        try:
            if not self.loopback_host():
                self.send(403, 'Use the loopback URL printed by the launcher.', 'text/plain; charset=utf-8')
                return
            action_path = urlsplit(self.path).path
            if action_path not in {'/api/warmup', '/api/terminal'}:
                self.send(405, 'This action is not available.', 'text/plain; charset=utf-8')
                return
            origin = self.headers.get('Origin')
            fetch_site = self.headers.get('Sec-Fetch-Site')
            expected = 'http://' + self.headers.get('Host', '')
            if origin != expected and fetch_site != 'same-origin':
                self.send(403, 'Actions require a same-origin browser request.', 'text/plain; charset=utf-8')
                return
            if self.headers.get_content_type() != 'application/json':
                self.send(415, 'Actions require JSON.', 'text/plain; charset=utf-8')
                return
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 16384:
                self.send(413, 'Action request is empty or too large.', 'text/plain; charset=utf-8')
                return
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError('Action request must be an object.')
            if action_path == '/api/warmup':
                result = self.explorer.warmup(request)
            else:
                action = request.pop('action', None)
                if action not in {'worker', 'start'}:
                    raise InvalidSelection('Terminal action must be worker or start.')
                if not self.terminal_launcher.available:
                    raise Unsupported('Automatic Terminal handoff is available only on macOS.')
                resolved = self.explorer.resolve(request)
                command = resolved.get(action)
                if not isinstance(command, str) or not command:
                    raise InvalidSelection(f'No {action} command is available for this selection.')
                result = self.terminal_launcher.launch(command)
                if result.get('state') not in {'prefilled', 'opened-copy-only'}:
                    self.send(422, json.dumps(result), 'application/json; charset=utf-8')
                    return
            self.send(200, json.dumps(result), 'application/json; charset=utf-8')
        except (InvalidSelection, Unsupported, ValueError, OSError, SyntaxError, TypeError, AttributeError, json.JSONDecodeError) as error:
            self.send(422, json.dumps(dict(error=str(error))), 'application/json; charset=utf-8')

    def loopback_host(self):
        return urlsplit('http://' + self.headers.get('Host', '')).hostname in {'127.0.0.1', 'localhost'}

    def log_message(self, *args):
        pass  # Keep local paths and selections out of access logs.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765, help='loopback port; 0 chooses an available port')
    parser.add_argument('--no-browser', action='store_true', help='print the URL without opening a browser')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(
            Handler, explorer=Explorer(root), terminal_launcher=MacTerminalLauncher()))
    except (OSError, OverflowError) as error:
        print(f'Could not start explorer: {error}. Try --port 0.', file=sys.stderr)
        return 1
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'Cadence AI Samples Explorer: {url}', flush=True)
    print('Refresh to reread this checkout. Ctrl+C stops the explorer.', flush=True)
    if not args.no_browser:
        def open_browser():
            try:
                if not webbrowser.open(url):
                    print('Open the URL above in your browser.', flush=True)
            except webbrowser.Error:
                print('Open the URL above in your browser.', flush=True)
        threading.Thread(target=open_browser, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nExplorer stopped.', flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
