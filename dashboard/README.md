# Live USB dashboard

Run on macOS or Linux using **Python 3.8+**. No pip packages, internet connection, or firmware changes are required. The interface uses browser-native JavaScript and SVG; all assets are local. Windows is not supported by this serial reader.

1. Connect the ESP32 and close Arduino Serial Monitor/Plotter and the standalone CSV logger.
2. From the repository folder, run `python3 dashboard/server.py`.
3. Open <http://127.0.0.1:8765> in a current browser.
4. Click **Start recording** to save new readings. Click **Stop recording** to finish. The filename and row count appear below the button.
5. Press **Control+C** in Terminal to shut down and close any active recording.

Alternative port: `python3 dashboard/server.py --port /dev/cu.usbserial-0001 --http-port 8765`.

The server listens only on your computer's loopback address. It automatically retries USB connection failures every two seconds. Only one program should read the ESP32 at a time. Multiple browser tabs share the same dashboard recording.

The dashboard keeps up to 1,500 readings in memory (about five minutes). Graphs show local receive times and break across long gaps. Current state becomes unavailable after three seconds without a complete motion cycle. Temperature becomes unavailable after five seconds without a new reading or after a missing-probe message. This is a view of the sketch's reported state, not an independent safety assessment.

CSV files are saved to the repository's ignored `logs/` folder with unique names. They use the standalone logger's columns. Blank temperature cells mean no new temperature in that motion cycle. Recording saves only newly completed cycles, not the earlier graph history. On USB loss, recording remains open and resumes on reconnection; gaps are visible in timestamps. Files flush after every row. The unfinished final serial cycle is not recorded.

The parser is shared with `tools/log_sensors.py`. The ESP32 still controls its LEDs and buzzer independently of the dashboard.
