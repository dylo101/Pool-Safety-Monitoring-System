import unittest
import json
from log_sensors import Parser, reading_row


class ParserTests(unittest.TestCase):
    def payload(self):
        return dict(protocol_version=2, sequence=10, state='ARMED', motion_score=.12,
                    motion_threshold=.5, temperature_c=None, temperature_f=None,
                    arming_remaining_ms=0, command_id=4, command_ok=True, command_error='')

    def test_json_cycle_is_immediate_and_includes_threshold(self):
        p = Parser()
        payload = self.payload()
        row = p.feed(json.dumps(payload))
        self.assertEqual(row['state'], 'ARMED')
        self.assertEqual(row['motion_threshold'], .5)
        self.assertEqual(row['temperature_f'], '')
        self.assertEqual(p.status['command_id'], 4)
        self.assertIsNone(p.feed('{bad json}'))

    def test_invalid_telemetry_is_not_presented_as_live(self):
        for key, value in [('state', 'SAFE'), ('motion_score', float('nan')),
                           ('motion_score', True), ('motion_threshold', 0),
                           ('arming_remaining_ms', -1), ('command_ok', 'true'),
                           ('command_id', True), ('sequence', False),
                           ('protocol_version', 3)]:
            payload = self.payload()
            payload[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                reading_row(payload)

    def test_alert_overrides_still_and_temperature_is_not_carried_forward(self):
        p = Parser()
        for line in ["Motion Score: 0.12", "STILL", "!!! STILL TOO LONG !!!",
                     "Probe Temperature: 20.00 C / 68.00 F"]:
            self.assertIsNone(p.feed(line))
        row = p.feed("Motion Score: 2.10")
        self.assertEqual(row["state"], "ALERT")
        self.assertEqual(row["temperature_f"], "68.00")
        p.feed("MOVING")
        row = p.feed("Motion Score: 0.10")
        self.assertEqual(row["state"], "MOVING")
        self.assertEqual(row["temperature_f"], "")

    def test_partial_start_and_disconnected_temperature(self):
        p = Parser()
        self.assertIsNone(p.feed("STILL"))
        p.feed("Motion Score: 0.10")
        p.feed("STILL")
        p.feed("Temperature sensor not detected. Check DAT, VCC and GND.")
        row = p.feed("Motion Score: 0.20")
        self.assertEqual(row["state"], "STILL_WAITING")
        self.assertEqual(row["temperature_c"], "")


if __name__ == "__main__":
    unittest.main()
