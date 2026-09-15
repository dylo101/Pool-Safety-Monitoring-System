#!/usr/bin/env python3
"""Log the existing pool sketch's serial output on macOS/Linux; no packages needed."""
import argparse
import csv
from datetime import datetime
import os
from pathlib import Path
import re
import select
import sys
import termios
import time

FIELDS = ["time", "motion_score", "state", "temperature_c", "temperature_f"]
NUMBER = r"-?\d+(?:\.\d+)?"
TEMP = re.compile(rf"Probe Temperature: ({NUMBER}) C / ({NUMBER}) F")


class Parser:
    def __init__(self):
        self.row = None

    def feed(self, line):
        completed = None
        if line.startswith("Motion Score: "):
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
