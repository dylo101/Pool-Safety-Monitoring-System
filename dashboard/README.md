# Pool activity dashboard

The dashboard runs on your Mac and supports USB or Wi-Fi. It controls the alarm on the ESP32; the ESP32 continues monitoring and sounding a latched alarm if the dashboard or network disconnects. Optional phone notifications use the Mac as an internet bridge to ntfy. Viewing the dashboard from outside the property is not implemented.

## Upload the new firmware first

Open `Arduino/PoolSafety_MotionDetection/PoolSafety_MotionDetection.ino` in Arduino IDE and upload it using your usual ESP32 board and port. The accompanying `ActivityAlarm.h` must stay in the sketch folder. Stop the USB dashboard/logger and close Serial Monitor before uploading. Restart the dashboard after uploading.

On power-up/reset, the device starts **disarmed**, and the threshold returns to **0.50**. The previous 15-second stillness rule is removed.

## USB option

Leave the ESP32 connected by USB, close Serial Monitor/Plotter and the standalone logger, then run:

```bash
cd ~/Pool-Safety-Monitoring-System
python3 dashboard/server.py --port /dev/cu.usbserial-0001
```

Open <http://127.0.0.1:8765>. USB readings, controls, and recording do not need Wi-Fi or internet. Phone notifications need internet on the Mac. If the serial port changes, use its current name. Press Control+C to stop the server.

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

## Phone notifications (iPhone)

The path is **ESP32 → USB or local Wi-Fi → Mac dashboard → ntfy.sh → iPhone**. Your phone can use cellular data or a different Wi-Fi network. Keep the Mac awake, online, and running the dashboard; its browser tab can be closed. No router port forwarding is needed. This addition does not require another firmware upload if the activity-alarm sketch is already running.

1. Install [ntfy for iPhone](https://apps.apple.com/us/app/ntfy/id1625396347) and allow notifications.
2. Stop the running dashboard with Control+C, restart it using the same USB or Wi-Fi command above, and refresh <http://127.0.0.1:8765>.
3. Under **Phone notifications**, click **Open phone setup**. This generates a long random topic; setup alone does not send messages or enable alarms.
4. In ntfy, add a subscription to that exact topic on the default server **https://ntfy.sh**. Use **Copy topic** to avoid typing errors. Keep this topic out of public screenshots, repositories, and demo videos: anyone who knows the topic can subscribe or publish. This channel has no account-based access control.
5. Close ntfy or lock your phone. Click **Send test notification**, then check that the phone receives it. A dashboard message saying ntfy accepted it confirms only service acceptance. If nothing arrives, verify the topic, notification permission, internet access, and iPhone Focus settings. See [ntfy's phone documentation](https://docs.ntfy.sh/subscribe/phone/).
6. Click **Enable alarm notifications**. Hide setup before recording a public demo.
7. Arm monitoring, allow settling, and gently disturb the water. Verify the local alarm and a phone notification. Leave the alarm active for 20 seconds and confirm repeated readings do not queue additional messages. Acknowledge, rearm, and repeat to verify a second event.
8. For a remote demo, put the iPhone on cellular data while keeping the Mac online. Record the disturbance, dashboard, local buzzer, and arriving phone alert. The phone cannot open this localhost dashboard remotely.

Alarm notifications start **off**. The topic and enabled preference persist in `dashboard/notification_settings.json`, an owner-readable file excluded from Git. Only opening setup reveals the topic; it is omitted from normal status responses and application logs. Alert messages contain a generic disturbance description and the Mac's observation time, not your sensor recordings or address. Messages are sent through the hosted ntfy service; its public-topic privacy model is described in the [publishing documentation](https://docs.ntfy.sh/publish/).

The sender reacts to the first `ALERT` reading from the activity-alarm firmware. It sends once while that alarm remains active, including through a dashboard connection interruption. Acknowledging and later triggering another alarm permits another message. Old stillness-firmware alerts do not send notifications. Enabling notifications during an existing alarm, or restarting the dashboard while an alarm is active, can notify again. A lost sensor connection is displayed on the dashboard but does not produce a separate phone notification.

Publishing runs in a separate worker with up to three attempts for transient failures. Requests time out after five seconds; retries wait two and then five seconds. Failure is shown in the phone panel and does not stop sensor reading, controls, recording, or the ESP32 alarm. If a service accepted a request but its response was lost, a retry may produce a duplicate phone message. Turning notifications off cancels queued jobs; an already submitted request cannot be recalled. At most 20 jobs wait in memory; jobs older than two minutes are discarded and do not survive a server restart. The panel reports failures and pending jobs, rather than claiming phone receipt.

Phone delivery still needs verification on your actual iPhone. This is a portfolio notification demonstration, not validated pool safety coverage.

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

The C++ test exercises the same alarm controller used by the sketch, including settling, two-of-five detection, single-spike rejection, latching, acknowledgment, sensitivity bounds, and clock rollover. Python tests cover both telemetry formats, controls/acknowledgments, stale readings, request validation, recordings, notification opt-in, alarm deduplication, retries, failures, queue expiry, and nonblocking sensor reading. Notification tests mock the service and send no real messages. These checks supplement physical testing.
