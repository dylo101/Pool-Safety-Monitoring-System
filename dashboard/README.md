# Pool activity dashboard

The dashboard runs on your Mac and supports USB or Wi-Fi. It controls the alarm on the ESP32; the ESP32 continues monitoring and sounding a latched alarm if the dashboard or network disconnects. This is a local prototype. Access from outside the property and phone notifications are not implemented.

## Upload the new firmware first

Open `Arduino/PoolSafety_MotionDetection/PoolSafety_MotionDetection.ino` in Arduino IDE and upload it using your usual ESP32 board and port. The accompanying `ActivityAlarm.h` must stay in the sketch folder. Stop the USB dashboard/logger and close Serial Monitor before uploading. Restart the dashboard after uploading.

On power-up/reset, the device starts **disarmed**, and the threshold returns to **0.50**. The previous 15-second stillness rule is removed.

## USB option

Leave the ESP32 connected by USB, close Serial Monitor/Plotter and the standalone logger, then run:

```bash
cd ~/Pool-Safety-Monitoring-System
python3 dashboard/server.py --port /dev/cu.usbserial-0001
```

Open <http://127.0.0.1:8765>. No Wi-Fi or internet is needed. If the serial port changes, use its current name. Press Control+C to stop the server.

## Wi-Fi option

Copy `Arduino/PoolSafety_MotionDetection/wifi_secrets.example.h` to `wifi_secrets.h` in the same folder and enter your 2.4 GHz network name and password locally. This credentials file is ignored by Git. Firmware binaries also contain the credentials.

Upload the main sketch and open Serial Monitor at 115200 baud. Press EN/Reset and look for `Wi-Fi connected. ESP32 address:` followed by an IP address. Close Serial Monitor and run:

```bash
python3 dashboard/server.py --esp32 YOUR_ESP32_IP
```

The Mac and ESP32 must share a reachable local network. The router may assign a new address after a restart. Switching USB/Wi-Fi modes requires stopping and restarting the dashboard. The device needs power in either mode.

The ESP32 provides measurements at `/readings` and accepts dashboard commands at `/control`. The dashboard server listens only on the Mac's loopback address. Both command endpoints require the custom `X-Pool-Dashboard: 1` header and provide no CORS access; this prevents ordinary cross-origin browser forms from issuing controls, but **is not authentication**. Anyone with direct network access and the protocol can control the ESP32. Use a trusted local network; do not forward these ports to the internet.

## Monitoring controls

| Control/state | Behavior |
|---|---|
| Arm monitoring | Starts a 10-second settling period; green LED blinks |
| Armed | Green LED stays on; two of the last five readings strictly above the device threshold trigger an alarm |
| Disarm | Cancels settling or armed monitoring; readings and recording continue |
| Acknowledge alarm | Clears a latched alarm and leaves monitoring disarmed |
| Movement threshold / Apply | Changes sensitivity while disarmed; accepts 0.10–20.00 in 0.01 increments |
| Sensor fault | Red LED on; check wiring with power off, then reset; an existing activity alarm stays latched |

The default threshold is 0.50. Lower values are more sensitive. Five samples span roughly one second; servicing Wi-Fi can add loop latency. Movement or calm water cannot automatically clear an alarm. An active alarm must be acknowledged before disarming or changing sensitivity.

Controls wait for confirmation from the ESP32. A timeout never implies success: check the displayed device state before retrying. Controls become unavailable when readings are stale. If the connection is lost, the dashboard cannot confirm or change the device state; it does not disarm the device. Arming and alarm detection require the updated firmware. Old firmware can still display readings, but controls stay disabled and its states are labeled as old firmware.

A motion sensor that fails during operation reports a fault until reset. If an alarm was already active, it stays active and can still be acknowledged. Reset/power loss clears the volatile state and starts disarmed.

## Graphs, events, and recording

The dashboard retains up to 1,500 readings and the last 100 monitoring state changes in memory for the current server session. Restarting the server clears that history, but does not change a still-powered ESP32's state. The event list reports when the dashboard observed each transition; Wi-Fi polling can miss short transitions. A latched alarm remains visible at subsequent polls.

Use **Start recording** before a trial and **Stop recording** afterward. Each recording creates a unique CSV under `logs/`. It contains time, motion score, state, latest temperature, and the threshold at that reading. CSVs record sensor samples; they are not a separate durable alarm-event journal. The dashed graph line follows the threshold stored in each sample.

Both current USB and Wi-Fi telemetry contain the latest temperature value at each sample. Missing-probe values are blank in CSVs. Old USB text telemetry retains blanks between temperature updates and its previous state names. Old CSVs do not have a threshold column.

Graphs use computer receive times and break at long gaps. Current state is unavailable after three seconds without fresh readings. Temperature expires after five seconds without an update or immediately when the device reports it missing. Recording remains open over a connection interruption and saves again when readings return. Files flush per row. The JSON protocol saves complete cycles immediately; the legacy text parser saves a cycle when the next starts.

This server requires macOS/Linux and Python 3.8+ with no pip packages.

## Activity-alarm test

Keep the electronics dry and secured. Test the mechanical assembly without electronics before putting it on water. Use gentle controlled disturbances that do not splash the cup.

1. Upload the activity-alarm sketch, restart the dashboard in USB or Wi-Fi mode, and refresh the browser.
2. Verify **Disarmed** at startup. Moving the float must change readings without sounding the buzzer.
3. Set threshold to 0.50 while disarmed. Click **Apply** and verify device confirmation.
4. Start recording. Click **Arm monitoring** and verify blinking green / Settling, followed by Armed after 10 seconds.
5. Record 60 seconds of settled water. Note any false alarms.
6. Create five separate disturbances away from the float, without touching its arm, cup, cable, or support. Note the start time and size of each attempt.
7. When an alarm occurs, verify red LED and buzzer stay on after water settles. Click **Acknowledge alarm** and verify Disarmed, with the buzzer off.
8. Rearm and wait through settling for each next disturbance. Count detected trials and missed trials; a continuous latched alarm is not five detections.
9. While armed, interrupt the selected dashboard connection without removing device power. Verify local alarm behavior still works. Reconnect and check the actual device state.
10. Verify **Disarm** during settling and while armed. Then reset the ESP32 and verify it starts disarmed with threshold 0.50.
11. Stop recording. Label trial conditions and save outcomes. If testing 1.00–1.20, repeat the same disturbances rather than assuming the threshold ignores all small ripples.

The first tank recordings are summarized in the root README. They used the previous stillness firmware and do not validate this new activity alarm.

## Software checks

```bash
python3 -m unittest discover -s dashboard -p 'test_*.py'
python3 -m unittest discover -s tools -p 'test_*.py'
c++ -std=c++11 -Wall -Wextra -pedantic tests/activity_alarm_test.cpp -o /tmp/pool-activity-test
/tmp/pool-activity-test
```

The C++ test exercises the same alarm controller used by the sketch, including settling, two-of-five detection, single-spike rejection, latching, acknowledgment, sensitivity bounds, and clock rollover. Python tests cover both telemetry formats, controls/acknowledgments, stale readings, request validation, and recordings. These checks supplement the physical test above.
