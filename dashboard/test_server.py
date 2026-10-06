import csv
import json
import os
import select
from io import BytesIO
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import patch
from server import Monitor, Command, ControlError, handler_for
from log_sensors import reading_row


class MonitorTests(unittest.TestCase):
    def test_wifi_reader_accepts_json_without_serial(self):
        m = Monitor('unused', '192.168.1.2')
        payload = dict(sequence=12, state='ALERT', motion_score=.12,
                       temperature_c=20, temperature_f=68)
        original_accept = m.accept
        def accept_and_stop(row, status=None):
            original_accept(row, status)
            m.stop.set()
        m.accept = accept_and_stop
        with patch('server.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = BytesIO(json.dumps(payload).encode())
            m.run_wifi()
        snapshot = m.snapshot()
        self.assertEqual(snapshot['transport'], 'Wi-Fi')
        self.assertTrue(snapshot['connected'])
        self.assertEqual(snapshot['rows'][0]['state'], 'ALERT')
        self.assertEqual(snapshot['temperature'], 68)

    def row(self):
        return dict(time='2026-09-14T21:00:00-07:00', motion_score=.1,
                    state='ALERT', temperature_c='20.0', temperature_f='68.0')

    def test_stale_readings_and_missing_temperature(self):
        m = Monitor('test')
        m.accept(self.row())
        self.assertTrue(m.snapshot()['connected'])
        self.assertEqual(m.snapshot()['temperature'], 68)
        m.last = time.monotonic()-4
        m.temperature_time = time.monotonic()-6
        self.assertFalse(m.snapshot()['connected'])
        self.assertIsNone(m.snapshot()['temperature'])

    def test_record_only_while_active_and_preserve_blanks(self):
        with tempfile.TemporaryDirectory() as folder, patch('server.ROOT', Path(folder)):
            m = Monitor('test')
            m.accept(self.row())
            m.recording(True)
            first = m.record_name
            m.recording(True)
            self.assertEqual(first, m.record_name)
            row = self.row()
            row['temperature_c'] = row['temperature_f'] = ''
            m.accept(row)
            m.recording(False)
            m.accept(self.row())
            with (Path(folder)/'logs'/first).open() as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['temperature_f'], '')
            m.recording(True)
            self.assertNotEqual(first, m.record_name)
            m.recording(False)

    def payload(self, state='DISARMED', command_id=0, command_ok=True):
        return dict(protocol_version=2, sequence=1, state=state, motion_score=.12,
                    motion_threshold=.5, temperature_c=20, temperature_f=68,
                    arming_remaining_ms=10000 if state == 'ARMING' else 0,
                    command_id=command_id, command_ok=command_ok,
                    command_error='' if command_ok else 'Acknowledge the active alarm first')

    def test_controls_require_live_new_firmware(self):
        m = Monitor('test')
        with self.assertRaises(ControlError):
            m.control('arm')
        m.accept(self.row())
        with self.assertRaisesRegex(ControlError, 'Upload'):
            m.control('arm')
        for value in (float('nan'), float('inf'), True, .01, 21):
            with self.assertRaises(ControlError):
                m.control('threshold', value)

    def test_usb_control_waits_for_matching_device_ack(self):
        m = Monitor('test')
        payload = self.payload()
        m.accept(reading_row(payload), payload)
        result = {}
        def control():
            result['snapshot'] = m.control('arm')
        worker = threading.Thread(target=control)
        worker.start()
        command = m.commands.get(timeout=1)
        m.commands.put(command)
        with patch('server.os.write', side_effect=lambda fd, data: len(data)) as write:
            m.service_commands(fd=17)
        self.assertIn(b' arm\n', write.call_args.args[1])
        self.assertFalse(command.done.is_set())
        m.acknowledge_command(self.payload('ARMING', command.id + 1))
        self.assertFalse(command.done.is_set())
        payload = self.payload('ARMING', command.id)
        m.accept(reading_row(payload), payload)
        m.acknowledge_command(payload)
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result['snapshot']['device']['state'], 'ARMING')

    def test_usb_roundtrip_on_simulated_serial_port(self):
        master, slave = os.openpty()
        m = Monitor(os.ttyname(slave))
        payload = self.payload()
        reader = threading.Thread(target=m.run, daemon=True)
        simulator_stop = threading.Event()
        def simulate_device():
            pending = b''
            while not simulator_stop.is_set():
                if select.select([master], [], [], .05)[0]:
                    pending += os.read(master, 4096)
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        parts = line.decode().split()
                        if len(parts) >= 3 and parts[0] == 'CONTROL':
                            payload['command_id'] = int(parts[1])
                            payload['state'] = 'ARMING'
                            payload['arming_remaining_ms'] = 10000
                payload['sequence'] += 1
                os.write(master, (json.dumps(payload) + '\n').encode())
        reader.start()
        simulator = threading.Thread(target=simulate_device, daemon=True)
        simulator.start()
        try:
            deadline = time.monotonic() + 2
            while not m.snapshot()['connected'] and time.monotonic() < deadline:
                simulator_stop.wait(.02)
            self.assertTrue(m.snapshot()['connected'], m.snapshot()['error'])
            result = m.control('arm')
            self.assertEqual(result['device']['state'], 'ARMING')
            self.assertEqual(result['rows'][-1]['state'], 'ARMING')
        finally:
            simulator_stop.set()
            m.stop.set()
            reader.join(1)
            simulator.join(1)
            os.close(slave)
            os.close(master)

    def test_rejected_command_and_timeout_do_not_change_state(self):
        m = Monitor('test')
        payload = self.payload('ALERT')
        m.accept(reading_row(payload), payload)
        command = Command('disarm', None)
        m.pending_command = command
        m.acknowledge_command(self.payload('ALERT', command.id, False))
        self.assertIn('Acknowledge', str(command.error))
        self.assertEqual(m.snapshot()['device']['state'], 'ALERT')
        command = Command('arm', None)
        command.deadline = time.monotonic() - 1
        m.pending_command = command
        m.service_commands(fd=17)
        self.assertTrue(command.done.is_set())
        self.assertIn('acknowledgment', str(command.error))

    def test_wifi_control_posts_and_checks_acknowledgment(self):
        m = Monitor('unused', '192.168.1.2')
        command = Command('threshold', '1.20')
        m.commands.put(command)
        payload = self.payload(command_id=command.id)
        payload['motion_threshold'] = 1.2
        with patch('server.urllib.request.build_opener') as factory:
            opener = factory.return_value
            opener.open.return_value = BytesIO(json.dumps(payload).encode())
            m.service_commands(opener=opener)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertIn(b'action=threshold', request.data)
        self.assertTrue(command.done.is_set())
        self.assertIsNone(command.error)
        self.assertEqual(m.snapshot()['device']['motion_threshold'], 1.2)

    def test_events_are_state_changes_and_threshold_is_recorded(self):
        with tempfile.TemporaryDirectory() as folder, patch('server.ROOT', Path(folder)):
            m = Monitor('test')
            m.recording(True)
            for state in ('DISARMED', 'ARMING', 'ARMING', 'ARMED', 'ALERT', 'ALERT', 'DISARMED'):
                payload = self.payload(state)
                m.accept(reading_row(payload), payload)
            m.recording(False)
            self.assertEqual([e['state'] for e in m.snapshot()['events']],
                             ['DISARMED', 'ARMING', 'ARMED', 'ALERT', 'DISARMED'])
            with (Path(folder) / 'logs' / m.record_name).open() as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(rows[0]['motion_threshold'], '0.5')

    def test_post_requires_header_and_valid_body(self):
        class Socket:
            def __init__(self, request):
                self.request = BytesIO(request)
                self.output = BytesIO()
            def makefile(self, *args): return self.request
            def sendall(self, data): self.output.write(data)
        m = Monitor('test')
        for request, code in [
            (b'POST /api/control HTTP/1.0\r\nContent-Length: 16\r\n\r\n{"action":"arm"}', 403),
            (b'POST /api/control HTTP/1.0\r\nX-Pool-Dashboard: 1\r\nContent-Length: 2\r\n\r\n[]', 400),
            (b'POST /api/control HTTP/1.0\r\nX-Pool-Dashboard: 1\r\nContent-Length: 16\r\n\r\n{"action":"arm"}', 503),
        ]:
            connection = Socket(request)
            handler_for(m)(connection, ('127.0.0.1', 1), None)
            self.assertIn(f' {code} '.encode(), connection.output.getvalue().split(b'\r\n')[0])


if __name__ == '__main__':
    unittest.main()
