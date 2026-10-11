"""Opt-in ntfy publishing. All internet requests run outside the sensor reader."""
from datetime import datetime
import http.client
import json
import os
from pathlib import Path
import queue
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

SERVER = 'https://ntfy.sh'


class Notifications:
    def __init__(self, settings_path):
        self.path = Path(settings_path)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.jobs = queue.Queue(maxsize=20)
        self.topic = ''
        self.enabled = False
        self.active_alarm = False
        self.generation = 0
        self.pending = 0
        self.accepted = 0
        self.last_result = ''
        self.last_success = ''
        self.error = ''
        self.testing = False
        self.worker = None
        try:
            settings = json.loads(self.path.read_text())
            topic = settings['topic']
            if not isinstance(topic, str) or not re.fullmatch(r'pool-[0-9a-f]{32}', topic):
                raise ValueError('Invalid notification topic')
            if type(settings['enabled']) is not bool:
                raise ValueError('Invalid notification setting')
            self.topic, self.enabled = topic, settings['enabled']
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            self.error = 'Phone settings could not be loaded. Open phone setup to configure again.'

    def save(self, topic, enabled):
        # Atomic replacement with owner-only permissions; never print the topic.
        temporary = self.path.with_name(self.path.name + '.' + secrets.token_hex(6) + '.tmp')
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(dict(topic=topic, enabled=enabled), stream)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def prepare(self):
        with self.lock:
            if not self.topic:
                topic = 'pool-' + secrets.token_hex(16)
                self.save(topic, False)
                self.topic = topic
                self.enabled = False
            self.error = ''
            return dict(server=SERVER, topic=self.topic)

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Enabled must be true or false')
        with self.lock:
            if not self.topic:
                raise ValueError('Open phone setup and subscribe before enabling notifications')
            self.save(self.topic, enabled)
            if enabled != self.enabled:
                self.generation += 1
                self.active_alarm = False
            self.enabled = enabled
            self.error = ''
            self.last_result = 'Alarm notifications enabled.' if enabled else 'Alarm notifications off.'

    def snapshot(self):
        with self.lock:
            # Topic is disclosed only by the explicit setup endpoint, not status/logs.
            return dict(configured=bool(self.topic), enabled=self.enabled, pending=self.pending,
                        accepted=self.accepted, last_result=self.last_result,
                        last_success=self.last_success, error=self.error, testing=self.testing)

    def enqueue(self, kind, detected_at):
        with self.lock:
            if not self.topic:
                raise ValueError('Open phone setup first')
            if kind == 'test' and self.testing:
                raise ValueError('A test notification is already pending')
            job = dict(kind=kind, detected_at=detected_at, topic=self.topic,
                       generation=self.generation, created=time.monotonic())
            try:
                self.jobs.put_nowait(job)
            except queue.Full:
                self.error = 'Phone notification queue is full; a notification was not queued.'
                raise ValueError(self.error)
            self.pending += 1
            self.testing = self.testing or kind == 'test'
            self.last_result = 'Test notification queued.' if kind == 'test' else 'Activity alarm queued for phone.'

    def test(self):
        self.enqueue('test', datetime.now().astimezone().isoformat(timespec='seconds'))

    def observe(self, state, detected_at):
        with self.lock:
            active = state == 'ALERT'
            rising = active and not self.active_alarm
            self.active_alarm = active
            if self.enabled and rising:
                try:
                    self.enqueue('alarm', detected_at)
                except ValueError:
                    pass  # Visible notification error; never stop local monitoring.

    def start(self):
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def close(self):
        self.stop.set()
        if self.worker:
            self.worker.join(6)

    def send(self, job, opener):
        test = job['kind'] == 'test'
        title = 'Pool monitor test' if test else 'Pool activity detected'
        message = ('Test notification from your pool monitoring prototype. No alarm was triggered.' if test else
                   'Unexpected water disturbance detected. Check the pool or test tank. '
                   'This notification does not confirm a person entered the water.')
        message += '\nObserved at ' + job['detected_at'] + '.'
        payload = dict(topic=job['topic'], title=title, message=message,
                       priority=3 if test else 4, tags=['test_tube' if test else 'warning'])
        request = urllib.request.Request(SERVER + '/', data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json'}, method='POST')
        with opener.open(request, timeout=5) as response:
            result = json.loads(response.read(8192))
        if (not isinstance(result, dict) or result.get('event') != 'message' or
                result.get('topic') != job['topic'] or not isinstance(result.get('id'), str) or not result['id']):
            raise ValueError('Unexpected notification-service response')

    def process(self, job, opener):
        error = ''
        try:
            for attempt in range(3):
                with self.lock:
                    canceled = (self.stop.is_set() or job['generation'] != self.generation or
                                (job['kind'] == 'alarm' and not self.enabled))
                if canceled:
                    return
                if time.monotonic() - job['created'] > 120:
                    error = 'Notification expired before it could be sent.'
                    break
                try:
                    self.send(job, opener)
                    with self.lock:
                        self.accepted += 1
                        self.last_success = datetime.now().astimezone().isoformat(timespec='seconds')
                        self.error = ''
                        self.last_result = 'ntfy accepted the notification. Check your phone to confirm receipt.'
                    return
                except urllib.error.HTTPError as exc:
                    error = f'Notification service returned HTTP {exc.code}.'
                    exc.close()
                    if exc.code not in (408, 429) and exc.code < 500:
                        break
                except (OSError, ValueError, http.client.HTTPException):
                    # Do not expose request bodies, topic names, or remote error content.
                    error = 'Notification could not be confirmed. Check internet access and retry the test.'
                if attempt < 2 and self.stop.wait(2 if attempt == 0 else 5):
                    return
            with self.lock:
                self.error = error
                self.last_result = 'Phone notification failed; the local alarm is unaffected.'
        finally:
            with self.lock:
                self.pending -= 1
                if job['kind'] == 'test':
                    self.testing = False

    def run(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while not self.stop.is_set():
            try:
                job = self.jobs.get(timeout=.2)
            except queue.Empty:
                continue
            self.process(job, opener)
