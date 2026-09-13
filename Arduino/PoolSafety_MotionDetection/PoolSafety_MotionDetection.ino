#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <OneWire.h>
#include <DallasTemperature.h>

Adafruit_MPU6050 mpu;

const int greenLED = 23;
const int redLED = 18;
const int buzzer = 19;
const int temperaturePin = 4;

OneWire oneWire(temperaturePin);
DallasTemperature temperatureSensor(&oneWire);
unsigned long temperatureRequestTime = 0;
const unsigned long temperatureInterval = 1000;

void updateTemperature() {
  // A 12-bit conversion takes up to 750 ms. Read after one second
  // without making motion detection wait for the conversion.
  if (millis() - temperatureRequestTime < temperatureInterval) {
    return;
  }

  float temperatureC = temperatureSensor.getTempCByIndex(0);
  if (temperatureC == DEVICE_DISCONNECTED_C) {
    Serial.println("Temperature sensor not detected. Check DAT, VCC and GND.");
  } else {
    Serial.print("Probe Temperature: ");
    Serial.print(temperatureC, 2);
    Serial.print(" C / ");
    Serial.print(DallasTemperature::toFahrenheit(temperatureC), 2);
    Serial.println(" F");
  }

  temperatureSensor.requestTemperatures();
  temperatureRequestTime = millis();
}

float previousX = 0;
float previousY = 0;
float previousZ = 0;
bool firstReading = true;

// Stillness timer
unsigned long stillStartTime = 0;
bool stillTimerRunning = false;

const unsigned long stillTime = 15000;  // 15 seconds

void setup() {

  pinMode(greenLED, OUTPUT);
  pinMode(redLED, OUTPUT);
  pinMode(buzzer, OUTPUT);
  digitalWrite(buzzer, LOW);

  Serial.begin(115200);

  while (!Serial) {
    delay(10);
  }

  if (!mpu.begin()) {
    Serial.println("MPU6050 not found!");
    while (1) {
      delay(10);
    }
  }

  Serial.println("MPU6050 Ready!");

  temperatureSensor.begin();
  temperatureSensor.setResolution(12);
  temperatureSensor.setWaitForConversion(false);
  temperatureSensor.requestTemperatures();
  temperatureRequestTime = millis();
  Serial.println("Temperature readings enabled on D4.");
}

void loop() {

  sensors_event_t accel;
  sensors_event_t gyro;
  sensors_event_t temp;

  mpu.getEvent(&accel, &gyro, &temp);

  float deltaX = accel.acceleration.x - previousX;
  float deltaY = accel.acceleration.y - previousY;
  float deltaZ = accel.acceleration.z - previousZ;

  float motion =
    abs(deltaX) +
    abs(deltaY) +
    abs(deltaZ);

  if (firstReading) {
    motion = 0;
    firstReading = false;
  }

  previousX = accel.acceleration.x;
  previousY = accel.acceleration.y;
  previousZ = accel.acceleration.z;

  Serial.print("Motion Score: ");
  Serial.println(motion);

  // MOVING
  if (motion > 0.5) {

    Serial.println("MOVING");

    // Reset stillness timer
    stillTimerRunning = false;

    // Green = person is moving
    digitalWrite(greenLED, HIGH);
    digitalWrite(redLED, LOW);
    digitalWrite(buzzer, LOW);


  }

  // STILL
  else {

    Serial.println("STILL");

    // Start the stillness timer
    if (!stillTimerRunning) {
      stillStartTime = millis();
      stillTimerRunning = true;

      Serial.println("Stillness timer started!");
    }

    // Check how long the person has been still
    unsigned long stillDuration = millis() - stillStartTime;

    if (stillDuration >= stillTime) {

      // Still for 15+ seconds = RED + buzzer
      digitalWrite(greenLED, LOW);
      digitalWrite(redLED, HIGH);
      digitalWrite(buzzer, HIGH);


      Serial.println("!!! STILL TOO LONG !!!");

    } else {

      // Still for less than 15 seconds = stay GREEN
      digitalWrite(greenLED, HIGH);
      digitalWrite(redLED, LOW);
      digitalWrite(buzzer, LOW);

    }
  }

  updateTemperature();
  delay(200);
}
