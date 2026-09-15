import unittest
from log_sensors import Parser


class ParserTests(unittest.TestCase):
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
