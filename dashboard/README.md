# Live USB dashboard

## Wi-Fi option

Copy `Arduino/PoolSafety_MotionDetection/wifi_secrets.example.h` to `wifi_secrets.h` in the same folder and enter your 2.4 GHz network name and password locally. The credentials file is ignored by Git. Never commit it or share firmware binaries, which also contain the credentials.

Upload the main sketch, then open Serial Monitor at 115200 baud. Look for `Wi-Fi connected. ESP32 address:` followed by an IP address. Start the dashboard with `python3 dashboard/server.py --esp32 YOUR_ESP32_IP` using that address. The Mac and ESP32 must be on the same reachable local network. The router may assign a new address after a restart.

The ESP32 exposes read-only measurements at `http://YOUR_ESP32_IP/readings` to devices on that network without authentication. Use a trusted local network; no router port forwarding is required. The dashboard still listens only on the Mac's loopback address.

Wi-Fi mode polls the latest measurement, so it may skip motion samples or brief transitions. It is not a lossless event log. Temperature columns contain the latest probe value at each poll, unlike USB mode where they are blank between probe updates. Stale/failed connections become unavailable, and polling retries automatically. USB remains available by starting without `--esp32`; switching transport requires restarting the dashboard. The ESP32 always needs power, even when readings travel over Wi-Fi.

Wi-Fi connection attempts do not wait in a connection loop. LEDs, buzzer, and serial output continue when Wi-Fi is unavailable. Network request servicing can still add loop latency; repeat the motion/15-second alert test after uploading.

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
