import csv
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from server import Monitor


class MonitorTests(unittest.TestCase):
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
