#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <WiFi.h>
#include <WebServer.h>
#include "ActivityAlarm.h"
#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#else
const char* WIFI_SSID = "";
const char* WIFI_PASSWORD = "";
#endif

const int greenLED = 23;
const int redLED = 18;
const int buzzer = 19;
const int temperaturePin = 4;
Adafruit_MPU6050 mpu;
OneWire oneWire(temperaturePin);
DallasTemperature temperatureSensor(&oneWire);
WebServer sensorServer(80);
ActivityAlarm activity;

bool sensorReady = false;
bool temperatureValid = false;
bool firstReading = true;
bool wifiWasConnected = false;
float previousX = 0, previousY = 0, previousZ = 0;
float latestMotion = 0, latestTemperature = 0;
uint32_t sampleSequence = 0, temperatureRequestTime = 0, wifiRetryTime = 0;
uint32_t commandId = 0;
bool commandOk = true;
const char* commandError = "";
String serialCommand;
bool discardSerialLine = false;

void updateOutputs() {
  bool alert = activity.state == ActivityAlarm::ALERT;
  bool green = sensorReady && (activity.state == ActivityAlarm::ARMED ||
      (activity.state == ActivityAlarm::ARMING && (millis() / 500) % 2 == 0));
  digitalWrite(greenLED, green ? HIGH : LOW);
  digitalWrite(redLED, (!sensorReady || alert) ? HIGH : LOW);
  digitalWrite(buzzer, alert ? HIGH : LOW);
}

String readingsJson() {
  String json = "{\"protocol_version\":2,\"sequence\":" + String(sampleSequence);
  json += ",\"motion_score\":" + String(latestMotion, 2);
  json += ",\"state\":\"" + String(sensorReady || activity.state == ActivityAlarm::ALERT ? activity.name() : "FAULT") + "\"";
  json += ",\"sensor_ok\":" + String(sensorReady ? "true" : "false");
  json += ",\"motion_threshold\":" + String(activity.threshold, 2);
  json += ",\"arming_remaining_ms\":" + String(activity.remaining(millis()));
  json += ",\"temperature_c\":" + (temperatureValid ? String(latestTemperature, 2) : String("null"));
  json += ",\"temperature_f\":" + (temperatureValid ? String(DallasTemperature::toFahrenheit(latestTemperature), 2) : String("null"));
  json += ",\"command_id\":" + String(commandId);
  json += ",\"command_ok\":" + String(commandOk ? "true" : "false");
  json += ",\"command_error\":\"" + String(commandError) + "\"}";
  return json;
}

bool parseId(const String& text, uint32_t& id) {
  if (!text.length() || text.length() > 10) return false;
  for (unsigned int i = 0; i < text.length(); ++i) if (!isDigit(text[i])) return false;
  unsigned long long value = strtoull(text.c_str(), nullptr, 10);
  if (!value || value > 0x7fffffffULL) return false;
  id = uint32_t(value);
  return true;
}

void applyCommand(uint32_t id, const String& action, const String& value) {
  commandId = id;
  commandOk = false;
  commandError = "Command unavailable in current state";
  if (action == "arm") {
    if (!sensorReady) commandError = "Motion sensor unavailable; reset after checking wiring";
    else commandOk = activity.arm(millis());
  } else if (action == "disarm") {
    commandOk = activity.disarm();
    if (!commandOk) commandError = "Acknowledge the active alarm first";
  } else if (action == "acknowledge") {
    commandOk = activity.acknowledge();
  } else if (action == "threshold") {
    char* end = nullptr;
    float threshold = strtof(value.c_str(), &end);
    if (!value.length() || end == value.c_str() || *end || !isfinite(threshold) ||
        threshold < 0.1f || threshold > 20.0f) {
      commandError = "Threshold must be between 0.10 and 20.00";
    } else {
      commandOk = activity.setThreshold(roundf(threshold * 100) / 100);
      if (!commandOk) commandError = "Disarm before changing sensitivity";
    }
  } else {
    commandError = "Unknown command";
  }
  if (commandOk) commandError = "";
  updateOutputs();
}

void serviceSerial() {
  // Bound input size and work per cycle; malformed input cannot stall sampling.
  for (int count = 0; count < 128 && Serial.available(); ++count) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (!discardSerialLine && serialCommand.startsWith("CONTROL ")) {
        int first = serialCommand.indexOf(' ', 8);
        uint32_t id;
        if (first > 8 && parseId(serialCommand.substring(8, first), id)) {
          int second = serialCommand.indexOf(' ', first + 1);
          String action = second < 0 ? serialCommand.substring(first + 1) :
              serialCommand.substring(first + 1, second);
          String value = second < 0 ? "" : serialCommand.substring(second + 1);
          applyCommand(id, action, value);
          Serial.println(readingsJson());
        }
      }
      serialCommand = "";
      discardSerialLine = false;
    } else if (!discardSerialLine) {
      if (serialCommand.length() < 95) serialCommand += c;
      else { serialCommand = ""; discardSerialLine = true; }
    }
  }
}

void serviceWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    if (!wifiWasConnected) {
      Serial.print("Wi-Fi connected. ESP32 address: ");
      Serial.println(WiFi.localIP());
      wifiWasConnected = true;
    }
    sensorServer.handleClient();
  } else {
    if (wifiWasConnected) Serial.println("Wi-Fi disconnected; USB and alerts remain active.");
    wifiWasConnected = false;
    if (strlen(WIFI_SSID) && millis() - wifiRetryTime >= 15000) {
      wifiRetryTime = millis();
      WiFi.reconnect();
    }
  }
}

void setup() {
  pinMode(greenLED, OUTPUT);
  pinMode(redLED, OUTPUT);
  pinMode(buzzer, OUTPUT);
  digitalWrite(greenLED, LOW);
  digitalWrite(redLED, LOW);
  digitalWrite(buzzer, LOW);
  Serial.begin(115200);
  sensorReady = mpu.begin();
  Serial.println(sensorReady ? "MPU6050 Ready!" : "MPU6050 not found!");
  updateOutputs();
  temperatureSensor.begin();
  temperatureSensor.setResolution(12);
  temperatureSensor.setWaitForConversion(false);
  temperatureSensor.requestTemperatures();
  temperatureRequestTime = millis();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  if (strlen(WIFI_SSID)) WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  sensorServer.on("/readings", HTTP_GET, []() {
    sensorServer.sendHeader("Cache-Control", "no-store");
    sensorServer.send(200, "application/json", readingsJson());
  });
  const char* headers[] = {"X-Pool-Dashboard"};
  sensorServer.collectHeaders(headers, 1);
  sensorServer.on("/control", HTTP_POST, []() {
    if (sensorServer.header("X-Pool-Dashboard") != "1") {
      sensorServer.send(403, "application/json", "{\"error\":\"Dashboard requests only\"}");
      return;
    }
    uint32_t id;
    if (!parseId(sensorServer.arg("id"), id)) {
      sensorServer.send(400, "application/json", "{\"error\":\"Invalid command ID\"}");
      return;
    }
    applyCommand(id, sensorServer.arg("action"), sensorServer.arg("value"));
    sensorServer.sendHeader("Cache-Control", "no-store");
    sensorServer.send(200, "application/json", readingsJson());
  });
  sensorServer.begin();
}

void loop() {
  serviceSerial();
  serviceWifi();
  if (sensorReady) {
    sensors_event_t accel, gyro, temp;
    if (!mpu.getEvent(&accel, &gyro, &temp)) {
      sensorReady = false;
    } else {
      latestMotion = firstReading ? 0 :
          fabsf(accel.acceleration.x - previousX) +
          fabsf(accel.acceleration.y - previousY) +
          fabsf(accel.acceleration.z - previousZ);
      firstReading = false;
      previousX = accel.acceleration.x;
      previousY = accel.acceleration.y;
      previousZ = accel.acceleration.z;
      if (!isfinite(latestMotion)) { latestMotion = 0; sensorReady = false; }
      else activity.sample(latestMotion, millis());
    }
  }
  if (millis() - temperatureRequestTime >= 1000) {
    latestTemperature = temperatureSensor.getTempCByIndex(0);
    temperatureValid = latestTemperature != DEVICE_DISCONNECTED_C;
    temperatureSensor.requestTemperatures();
    temperatureRequestTime = millis();
  }
  updateOutputs();
  sampleSequence++;
  Serial.println(readingsJson());
  delay(200);
}
