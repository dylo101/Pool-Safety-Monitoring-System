"""Local USB dashboard. Python standard library only; macOS and Linux."""
import argparse
import csv
from collections import deque
from datetime import datetime
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import signal
import sys
import termios
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from log_sensors import Parser, FIELDS


class Monitor:
    def __init__(self, port):
        self.port = port
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.rows = deque(maxlen=1500)
        self.last = 0
        self.temperature = None
        self.temperature_time = 0
        self.error = 'Connecting to ESP32…'
        self.record_file = None
        self.writer = None
        self.record_name = ''
        self.record_count = 0

    def recording(self, start):
        with self.lock:
            if start and self.record_file is None:
                folder = ROOT / 'logs'
                folder.mkdir(exist_ok=True)
                name = 'dashboard_' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f') + '.csv'
                stream = (folder / name).open('x', newline='')
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader()
                stream.flush()
                self.record_file, self.writer = stream, writer
                self.record_name, self.record_count = name, 0
            elif not start and self.record_file:
                self.record_file.close()
                self.record_file = self.writer = None

    def accept(self, row):
        with self.lock:
            self.rows.append(row)
            self.last = time.monotonic()
            self.error = ''
            if row['temperature_f'] != '':
                self.temperature = float(row['temperature_f'])
                self.temperature_time = self.last
            if self.writer:
                try:
                    self.writer.writerow(row)
                    self.record_file.flush()
                    self.record_count += 1
                except OSError as exc:
                    self.recording(False)
                    self.error = f'Recording stopped: {exc}'

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            return dict(rows=list(self.rows), connected=bool(self.last and now-self.last < 3),
                        error=self.error, temperature=self.temperature if now-self.temperature_time < 5 else None,
                        recording=self.record_file is not None, filename=self.record_name,
                        count=self.record_count, port=self.port)

    def run(self):
        while not self.stop.is_set():
            fd = previous = None
            try:
                fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                fcntl.ioctl(fd, termios.TIOCEXCL)
                previous = termios.tcgetattr(fd)
                settings = termios.tcgetattr(fd)
                settings[0] = settings[1] = settings[3] = 0
                settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
                settings[4] = settings[5] = termios.B115200
                settings[6][termios.VMIN] = settings[6][termios.VTIME] = 0
                termios.tcsetattr(fd, termios.TCSANOW, settings)
                termios.tcflush(fd, termios.TCIFLUSH)
                parser, pending = Parser(), b''
                with self.lock:
                    self.error = 'Waiting for sensor readings…'
                while not self.stop.is_set():
                    if not select.select([fd], [], [], .5)[0]:
                        continue
                    data = os.read(fd, 4096)
                    if not data:
                        raise OSError('USB disconnected')
                    pending += data
                    if len(pending) > 65536:
                        pending = b''
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        line = line.decode('utf8', errors='replace').strip()
                        if 'Temperature sensor not detected' in line:
                            with self.lock:
                                self.temperature = None
                                self.temperature_time = 0
                        row = parser.feed(line)
                        if row:
                            self.accept(row)
            except (OSError, termios.error) as exc:
                with self.lock:
                    self.last = 0
                    self.temperature = None
                    self.error = f'USB unavailable: {exc}. Close Serial Monitor and the CSV logger. Retrying…'
            finally:
                if fd is not None:
                    try:
                        if previous is not None:
                            termios.tcsetattr(fd, termios.TCSANOW, previous)
                        fcntl.ioctl(fd, termios.TIOCNXCL)
                    except (OSError, termios.error):
                        pass
                    os.close(fd)
            self.stop.wait(2)


def handler_for(monitor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, mime='application/json'):
            payload = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == '/':
                self.reply(200, Path(__file__).with_name('index.html').read_bytes(), 'text/html; charset=utf-8')
            elif self.path == '/api/status':
                self.reply(200, monitor.snapshot())
            else:
                self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            # Custom header and no CORS prevent another website starting recordings.
            if self.headers.get('X-Pool-Dashboard') != '1':
                return self.reply(403, {'error': 'Dashboard requests only'})
            if self.path not in ('/api/record/start', '/api/record/stop'):
                return self.reply(404, {'error': 'Not found'})
            try:
                monitor.recording(self.path.endswith('/start'))
                self.reply(200, monitor.snapshot())
            except OSError as exc:
                self.reply(500, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/cu.usbserial-0001')
    parser.add_argument('--http-port', type=int, default=8765)
    args = parser.parse_args()
    monitor = Monitor(args.port)
    server = ThreadingHTTPServer(('127.0.0.1', args.http_port), handler_for(monitor))
    worker = threading.Thread(target=monitor.run, daemon=True)
    worker.start()
    def interrupt(*unused):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    print(f'Open http://127.0.0.1:{args.http_port} — stop with Control+C', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop.set()
        worker.join(3)
        monitor.recording(False)
        server.server_close()


if __name__ == '__main__':
    main()
