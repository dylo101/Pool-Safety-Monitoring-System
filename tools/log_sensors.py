#!/usr/bin/env python3
"""Log current JSON or legacy pool-sketch USB telemetry on macOS/Linux."""
import argparse
import csv
import json
import math
from datetime import datetime
import os
from pathlib import Path
import re
import select
import sys
import termios
import time

FIELDS = ["time", "motion_score", "state", "temperature_c", "temperature_f", "motion_threshold"]
ACTIVITY_STATES = ("DISARMED", "ARMING", "ARMED", "ALERT", "FAULT")
NUMBER = r"-?\d+(?:\.\d+)?"
TEMP = re.compile(rf"Probe Temperature: ({NUMBER}) C / ({NUMBER}) F")


def reading_row(payload):
    """Validate telemetry before presenting it as live data; accept old Wi-Fi firmware."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid telemetry")
    version = payload.get('protocol_version')
    states = ACTIVITY_STATES if version == 2 else ('MOVING', 'STILL_WAITING', 'ALERT', 'UNKNOWN')
    if payload.get('state') not in states or type(payload.get('sequence')) is not int:
        raise ValueError('Invalid sensor state')
    if version not in (None, 2):
        raise ValueError('Unsupported firmware protocol')
    for key in ('motion_score', 'temperature_c', 'temperature_f'):
        value = payload[key]
        if value is None and key != 'motion_score':
            continue
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Invalid sensor reading')
    if version == 2:
        threshold = payload.get('motion_threshold')
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not .1 <= threshold <= 20:
            raise ValueError('Invalid movement threshold')
        remaining, command_id = payload.get('arming_remaining_ms'), payload.get('command_id')
        if type(remaining) is not int or not 0 <= remaining <= 10000:
            raise ValueError('Invalid arming countdown')
        if type(command_id) is not int or not 0 <= command_id <= 2147483647:
            raise ValueError('Invalid command acknowledgment')
        if type(payload.get('command_ok')) is not bool or not isinstance(payload.get('command_error'), str):
            raise ValueError('Invalid command acknowledgment')
    row = {key: payload.get(key) if payload.get(key) is not None else '' for key in FIELDS if key != 'time'}
    row['time'] = datetime.now().astimezone().isoformat(timespec='milliseconds')
    return row


class Parser:
    def __init__(self):
        self.row = None
        self.status = None

    def feed(self, line):
        completed = None
        if line.startswith('{'):
            try:
                payload = json.loads(line)
                row = reading_row(payload)
            except (ValueError, KeyError, TypeError):
                return None
            self.status = payload
            self.row = None
            return row
        if line.startswith("Motion Score: "):
            self.status = None
            try:
                score = float(line.split(": ", 1)[1])
            except ValueError:
                return None
            completed = self.row
            self.row = dict.fromkeys(FIELDS, "")
            self.row.update(time=datetime.now().astimezone().isoformat(timespec="milliseconds"),
                            motion_score=score, state="UNKNOWN")
        elif line == "MPU6050 Ready!":
            completed, self.row = self.row, None
        elif self.row is not None:
            if line == "MOVING":
                self.row["state"] = "MOVING"
            elif line == "STILL":
                self.row["state"] = "STILL_WAITING"
            elif line == "!!! STILL TOO LONG !!!":
                self.row["state"] = "ALERT"
            elif match := TEMP.fullmatch(line):
                self.row["temperature_c"], self.row["temperature_f"] = match.groups()
        return completed


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--port", default="/dev/cu.usbserial-0001")
    cli.add_argument("--seconds", type=float, default=0,
                     help="Stop after this many seconds; default: run until Ctrl+C")
    args = cli.parse_args()
    if args.seconds < 0:
        cli.error("--seconds must be zero or positive")
    folder = Path(__file__).resolve().parents[1] / "logs"
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    csv_path = folder / f"pool_{stamp}.csv"
    raw_path = folder / f"pool_{stamp}_serial.txt"
    fd = None
    previous = None
    count = 0
    try:
        fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        # Reject another serial reader rather than competing for incoming bytes.
        import fcntl
        fcntl.ioctl(fd, termios.TIOCEXCL)
        previous = termios.tcgetattr(fd)
        settings = termios.tcgetattr(fd)
        settings[0] = settings[1] = settings[3] = 0
        settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        settings[4] = settings[5] = termios.B115200
        settings[6][termios.VMIN] = 0
        settings[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, settings)
        termios.tcflush(fd, termios.TCIFLUSH)
        folder.mkdir(exist_ok=True)
        parser = Parser()
        start = last_data = time.monotonic()
        pending = b""
        print(f"Recording {args.port} at 115200 baud. Stop with Ctrl+C.", flush=True)
        print(f"CSV: {csv_path}\nRaw serial: {raw_path}", flush=True)
        with csv_path.open("x", newline="") as output, raw_path.open("x") as raw:
            writer = csv.DictWriter(output, fieldnames=FIELDS)
            writer.writeheader()
            output.flush()
            try:
                while not args.seconds or time.monotonic() - start < args.seconds:
                    ready, _, _ = select.select([fd], [], [], 0.5)
                    if not ready:
                        if time.monotonic() - last_data > 10:
                            print("No serial data for 10 seconds. Check USB and close Serial Monitor.", flush=True)
                            last_data = time.monotonic()
                        continue
                    data = os.read(fd, 4096)
                    if not data:
                        raise OSError("Serial device disconnected")
                    last_data = time.monotonic()
                    pending += data
                    while b"\n" in pending:
                        line_bytes, pending = pending.split(b"\n", 1)
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        raw.write(f"{datetime.now().astimezone().isoformat()} {line}\n")
                        raw.flush()
                        row = parser.feed(line)
                        if row:
                            writer.writerow(row)
                            output.flush()
                            count += 1
                            if count == 1 or count % 25 == 0:
                                print(f"Saved {count} readings; state: {row['state']}", flush=True)
                        if "not detected" in line or "not found" in line:
                            print(line, flush=True)
            except KeyboardInterrupt:
                pass
            finally:
                # The unfinished last cycle stays in the raw log only.
                print(f"Saved {count} complete readings to {csv_path}", flush=True)
        return 0 if count else 1
    except (OSError, termios.error) as error:
        print(f"Cannot record: {error}. Check USB and close Serial Monitor/Plotter.", file=sys.stderr)
        return 1
    finally:
        if fd is not None:
            if previous is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, previous)
                except (OSError, termios.error):
                    pass
            os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
