import csv
import json
from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from server import Monitor


class MonitorTests(unittest.TestCase):
    def test_wifi_reader_accepts_json_without_serial(self):
        m = Monitor('unused', '192.168.1.2')
        payload = dict(sequence=12, state='ALERT', motion_score=.12,
                       temperature_c=20, temperature_f=68)
        original_accept = m.accept
        def accept_and_stop(row):
            original_accept(row)
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


if __name__ == '__main__':
    unittest.main()
