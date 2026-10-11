"""Local USB/Wi-Fi activity dashboard. Python standard library; macOS and Linux."""
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
import queue
import secrets
import urllib.request
import urllib.error
import urllib.parse
import ipaddress

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from log_sensors import Parser, FIELDS, reading_row
from notifications import Notifications


class ControlError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class Command:
    def __init__(self, action, value):
        self.id = secrets.randbelow(2147483647) + 1
        self.action, self.value = action, value
        self.done = threading.Event()
        self.deadline = time.monotonic() + 3
        self.error = None

    def finish(self, error=None):
        self.error = error
        self.done.set()


class Monitor:
    def __init__(self, port, host=None, notifications=None):
        self.port = port
        self.host = host
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
        self.device_status = None
        self.events = deque(maxlen=100)
        self.commands = queue.Queue()
        self.control_lock = threading.Lock()
        self.pending_command = None
        self.notifications = notifications

    def control(self, action, value=None):
        if action not in ('arm', 'disarm', 'acknowledge', 'threshold'):
            raise ControlError('Unknown command', 400)
        if action == 'threshold':
            if type(value) not in (int, float) or not .1 <= value <= 20:
                raise ControlError('Threshold must be between 0.10 and 20.00', 400)
            value = f'{value:.2f}'
        elif value is not None:
            raise ControlError('Unexpected command value', 400)
        if not self.control_lock.acquire(blocking=False):
            raise ControlError('Another control request is pending', 409)
        try:
            with self.lock:
                if not self.last or time.monotonic() - self.last >= 3:
                    raise ControlError('No live device connection')
                if not self.device_status or self.device_status.get('protocol_version') != 2:
                    raise ControlError('Upload the activity-alarm firmware to enable controls', 409)
            command = Command(action, value)
            self.commands.put(command)
            if not command.done.wait(4):
                raise ControlError('No device acknowledgment. Check the current state before retrying.')
            if command.error:
                raise command.error
            return self.snapshot()
        finally:
            self.control_lock.release()

    def acknowledge_command(self, payload):
        command = self.pending_command
        if command and payload.get('command_id') == command.id:
            error = None if payload['command_ok'] else ControlError(payload['command_error'], 409)
            command.finish(error)
            self.pending_command = None

    def service_commands(self, opener=None, fd=None):
        if self.pending_command:
            if time.monotonic() > self.pending_command.deadline:
                self.pending_command.finish(ControlError('No device acknowledgment. Check the current state before retrying.'))
                self.pending_command = None
            else:
                return
        try:
            command = self.commands.get_nowait()
        except queue.Empty:
            return
        if time.monotonic() > command.deadline:
            command.finish(ControlError('Command expired before it could be sent'))
            return
        self.pending_command = command
        try:
            if opener is not None:
                body = urllib.parse.urlencode(dict(id=command.id, action=command.action,
                                                   value=command.value or '')).encode()
                request = urllib.request.Request(f'http://{self.host}/control', data=body,
                                                 headers={'X-Pool-Dashboard': '1'}, method='POST')
                with opener.open(request, timeout=1.5) as response:
                    payload = json.loads(response.read(8192))
                row = reading_row(payload)
                if payload.get('command_id') != command.id:
                    raise ValueError('Device did not acknowledge this command')
                self.accept(row, payload)
                self.acknowledge_command(payload)
            else:
                value = '' if command.value is None else ' ' + command.value
                data = f'CONTROL {command.id} {command.action}{value}\n'.encode()
                if os.write(fd, data) != len(data):
                    raise OSError('Incomplete USB command; check state before retrying')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            command.finish(ControlError(f'Control not confirmed: {exc}. Check the current state before retrying.'))
            self.pending_command = None

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

    def accept(self, row, status=None):
        with self.lock:
            previous_state = self.rows[-1]['state'] if self.rows else None
            self.rows.append(row)
            self.last = time.monotonic()
            self.error = ''
            self.device_status = status
            if status and status.get('protocol_version') == 2 and row['state'] != previous_state:
                self.events.append(dict(time=row['time'], state=row['state'],
                                        motion_score=row['motion_score'],
                                        motion_threshold=row['motion_threshold']))
            if self.notifications and status and status.get('protocol_version') == 2:
                self.notifications.observe(row['state'], row['time'])
            if status and status['temperature_f'] is None:
                self.temperature = None
                self.temperature_time = 0
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
                        count=self.record_count, port=self.port,
                        device=self.device_status, events=list(self.events),
                        notifications=self.notifications.snapshot() if self.notifications else None,
                        transport='Wi-Fi' if self.host else 'USB')

    def run_wifi(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        previous_sequence = None
        while not self.stop.is_set():
            self.service_commands(opener=opener)
            try:
                with opener.open(f'http://{self.host}/readings', timeout=1.5) as response:
                    payload = json.loads(response.read(8192))
                row = reading_row(payload)
                sequence = payload['sequence']
                if sequence != previous_sequence:
                    self.accept(row, payload)
                    previous_sequence = sequence
            except (OSError, ValueError, KeyError, TypeError) as exc:
                with self.lock:
                    self.error = f'Wi-Fi readings unavailable: {exc}. Check ESP32 power and network.'
            self.stop.wait(.2)

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
                    self.service_commands(fd=fd)
                    if not select.select([fd], [], [], .1)[0]:
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
                            self.accept(row, parser.status)
                            if parser.status:
                                self.acknowledge_command(parser.status)
            except (OSError, termios.error) as exc:
                with self.lock:
                    self.last = 0
                    self.temperature = None
                    self.error = f'USB unavailable: {exc}. Close Serial Monitor and the CSV logger. Retrying…'
            finally:
                if self.pending_command:
                    self.pending_command.finish(ControlError('USB connection closed before acknowledgment'))
                    self.pending_command = None
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
            if self.path not in ('/api/record/start', '/api/record/stop', '/api/control',
                                 '/api/notifications/setup', '/api/notifications/enable',
                                 '/api/notifications/test'):
                return self.reply(404, {'error': 'Not found'})
            try:
                if self.path.startswith('/api/notifications/'):
                    if not monitor.notifications:
                        return self.reply(503, {'error': 'Restart the updated dashboard to enable phone setup'})
                    if self.path.endswith('/setup'):
                        return self.reply(200, monitor.notifications.prepare())
                    if self.path.endswith('/test'):
                        monitor.notifications.test()
                    else:
                        length = int(self.headers.get('Content-Length', '0'))
                        if not 0 < length <= 1024:
                            return self.reply(400, {'error': 'Invalid request size'})
                        body = json.loads(self.rfile.read(length))
                        if not isinstance(body, dict):
                            return self.reply(400, {'error': 'Invalid notification setting'})
                        monitor.notifications.set_enabled(body.get('enabled'))
                    return self.reply(200, monitor.snapshot())
                if self.path == '/api/control':
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 1024:
                        return self.reply(400, {'error': 'Invalid request size'})
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        return self.reply(400, {'error': 'Invalid command'})
                    return self.reply(200, monitor.control(payload.get('action'), payload.get('value')))
                monitor.recording(self.path.endswith('/start'))
                self.reply(200, monitor.snapshot())
            except ControlError as exc:
                self.reply(exc.status, {'error': str(exc)})
            except (ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except OSError as exc:
                self.reply(500, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/cu.usbserial-0001')
    parser.add_argument('--http-port', type=int, default=8765)
    parser.add_argument('--esp32', help='ESP32 IPv4 address; omit for USB')
    args = parser.parse_args()
    if args.esp32:
        try:
            ipaddress.IPv4Address(args.esp32)
        except ValueError:
            parser.error('--esp32 must be an IPv4 address')
    notifications = Notifications(Path(__file__).with_name('notification_settings.json'))
    monitor = Monitor(args.port, args.esp32, notifications)
    server = ThreadingHTTPServer(('127.0.0.1', args.http_port), handler_for(monitor))
    notifications.start()
    worker = threading.Thread(target=monitor.run_wifi if args.esp32 else monitor.run, daemon=True)
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
        notifications.close()
        server.server_close()


if __name__ == '__main__':
    main()
