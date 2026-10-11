from io import BytesIO
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error

from notifications import Notifications, SERVER
from server import Monitor, handler_for
from log_sensors import reading_row


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'settings.json'
        self.n = Notifications(self.path)

    def configure(self):
        self.n.prepare()
        self.n.set_enabled(True)

    def job(self):
        self.n.test()
        return self.n.jobs.get_nowait()

    def accepted_opener(self, topic=None):
        opener = Mock()
        opener.open.return_value = BytesIO(json.dumps(dict(event='message', id='abc123',
                                                          topic=topic or self.n.topic)).encode())
        return opener

    def test_setup_is_opt_in_private_persistent_and_does_not_publish(self):
        self.assertFalse(self.n.enabled)
        self.assertFalse(self.path.exists())
        setup = self.n.prepare()
        self.assertEqual(setup['server'], SERVER)
        self.assertRegex(setup['topic'], r'^pool-[0-9a-f]{32}$')
        self.assertEqual(self.n.prepare(), setup)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertNotIn(setup['topic'], json.dumps(self.n.snapshot()))
        self.assertEqual(self.n.pending, 0)
        self.n.set_enabled(True)
        restored = Notifications(self.path)
        self.assertTrue(restored.enabled)
        self.assertEqual(restored.topic, setup['topic'])

    def test_alert_dedup_rearm_and_no_old_stillness_alerts(self):
        self.n.prepare()
        self.n.observe('ALERT', 'now')
        self.assertEqual(self.n.pending, 0)
        self.n.set_enabled(True)
        m = Monitor('unused', notifications=self.n)
        legacy = dict(sequence=1, state='ALERT', motion_score=.1, temperature_c=20, temperature_f=68)
        m.accept(reading_row(legacy), legacy)
        self.assertEqual(self.n.pending, 0)
        for state in ('DISARMED', 'ARMING', 'ARMED', 'ALERT', 'ALERT', 'ALERT'):
            payload = dict(protocol_version=2, state=state, motion_score=1,
                           motion_threshold=.5, arming_remaining_ms=0, sequence=1,
                           command_id=0, command_ok=True, command_error='',
                           temperature_c=None, temperature_f=None)
            m.accept(reading_row(payload), payload)
        self.assertEqual(self.n.pending, 1)
        # Reconnecting while a latched alert is still active must not send again.
        m.last = time.monotonic() - 10
        m.accept(reading_row(payload), payload)
        self.assertEqual(self.n.pending, 1)
        self.n.observe('DISARMED', 'later')
        self.n.observe('ALERT', 'later')
        self.assertEqual(self.n.pending, 2)

    def test_test_notification_allowed_while_off_and_payload_is_generic(self):
        self.n.prepare()
        job = self.job()
        with self.assertRaisesRegex(ValueError, 'already pending'):
            self.n.test()
        opener = self.accepted_opener()
        self.n.process(job, opener)
        self.assertFalse(self.n.enabled)
        self.assertFalse(self.n.testing)
        self.assertEqual(self.n.accepted, 1)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, SERVER + '/')
        self.assertEqual(request.get_method(), 'POST')
        payload = json.loads(request.data)
        self.assertEqual(payload['priority'], 3)
        self.assertIn('No alarm was triggered', payload['message'])
        self.assertNotIn('127.0.0.1', payload['message'])
        self.assertIn('Check your phone', self.n.last_result)

    def test_worker_does_not_block_readings(self):
        self.configure()
        m = Monitor('unused', notifications=self.n)
        entered, release = threading.Event(), threading.Event()
        def slow_send(job, opener):
            entered.set()
            release.wait(2)
        with patch.object(self.n, 'send', side_effect=slow_send):
            self.n.start()
            try:
                self.n.observe('ALERT', 'now')
                self.assertTrue(entered.wait(1))
                row = dict(time='now', state='ALERT', motion_score=1,
                           temperature_c='', temperature_f='')
                m.accept(row)
                self.assertEqual(len(m.snapshot()['rows']), 1)
                self.assertEqual(self.n.pending, 1)
                self.assertEqual(self.n.accepted, 0)
            finally:
                release.set()
                self.n.close()
        self.assertFalse(self.n.worker.is_alive())

    def test_disable_cancels_previously_queued_messages(self):
        self.configure()
        self.n.observe('ALERT', 'now')
        job = self.n.jobs.get_nowait()
        self.n.set_enabled(False)
        opener = Mock()
        self.n.process(job, opener)
        opener.open.assert_not_called()
        self.assertEqual(self.n.pending, 0)
        self.assertEqual(self.n.accepted, 0)

    def test_expired_notifications_do_not_publish(self):
        self.n.prepare()
        job = self.job()
        job['created'] -= 121
        opener = Mock()
        self.n.process(job, opener)
        opener.open.assert_not_called()
        self.assertIn('expired', self.n.error)
        self.assertFalse(self.n.testing)

    def test_transient_failure_retries_and_persistent_failure_is_visible(self):
        self.n.prepare()
        for failure in (OSError('private detail'), http.client.RemoteDisconnected('private detail'),
                        urllib.error.HTTPError(SERVER, 503, 'private detail', {}, None)):
            opener = Mock()
            opener.open.side_effect = failure
            with patch.object(self.n.stop, 'wait', return_value=False):
                self.n.process(self.job(), opener)
            self.assertEqual(opener.open.call_count, 3)
            self.assertEqual(self.n.pending, 0)
            self.assertEqual(self.n.accepted, 0)
            self.assertTrue(self.n.error)
            self.assertNotIn('private detail', self.n.error)
        with patch.object(self.n.stop, 'wait', return_value=False), patch.object(
                self.n, 'send', side_effect=[OSError(), None]) as send:
            self.n.process(self.job(), Mock())
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.n.accepted, 1)
        self.assertFalse(self.n.error)

    def test_permanent_failure_and_invalid_service_response_are_not_success(self):
        self.n.prepare()
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(SERVER, 403, 'hidden', {}, None)
        self.n.process(self.job(), opener)
        self.assertEqual(opener.open.call_count, 1)
        self.assertIn('403', self.n.error)
        with patch.object(self.n.stop, 'wait', return_value=False):
            self.n.process(self.job(), self.accepted_opener('wrong-topic'))
        self.assertEqual(self.n.accepted, 0)
        self.assertTrue(self.n.error)

    def test_queue_full_is_visible_without_stopping_monitoring(self):
        self.configure()
        for _ in range(21):
            self.n.observe('ARMED', 'now')
            self.n.observe('ALERT', 'now')
        self.assertEqual(self.n.pending, 20)
        self.assertIn('full', self.n.error)

    def request(self, path, body=None, header=True):
        class Socket:
            def __init__(self, request):
                self.request, self.output = BytesIO(request), BytesIO()
            def makefile(self, *args): return self.request
            def sendall(self, data): self.output.write(data)
        content = json.dumps(body).encode() if body is not None else b''
        request = f'POST {path} HTTP/1.0\r\nContent-Length: {len(content)}\r\n'
        if header:
            request += 'X-Pool-Dashboard: 1\r\n'
        socket = Socket(request.encode() + b'\r\n' + content)
        handler_for(Monitor('unused', notifications=self.n))(socket, ('127.0.0.1', 1), None)
        head, data = socket.output.getvalue().split(b'\r\n\r\n', 1)
        return int(head.split()[1]), json.loads(data)

    def test_http_setup_enable_test_and_validation(self):
        self.assertEqual(self.request('/api/notifications/setup', header=False)[0], 403)
        self.assertEqual(self.request('/api/notifications/test')[0], 400)
        self.assertEqual(self.request('/api/notifications/enable', {'enabled': True})[0], 400)
        code, setup = self.request('/api/notifications/setup')
        self.assertEqual(code, 200)
        self.assertIn('topic', setup)
        self.assertEqual(self.n.pending, 0)
        for invalid in ({'enabled': 'true'}, {'enabled': 1}, [], {}):
            self.assertEqual(self.request('/api/notifications/enable', invalid)[0], 400)
        code, status = self.request('/api/notifications/enable', {'enabled': True})
        self.assertEqual(code, 200)
        self.assertTrue(status['notifications']['enabled'])
        self.assertNotIn(setup['topic'], json.dumps(status))
        code, status = self.request('/api/notifications/test')
        self.assertEqual(code, 200)
        self.assertEqual(status['notifications']['pending'], 1)


if __name__ == '__main__':
    unittest.main()
