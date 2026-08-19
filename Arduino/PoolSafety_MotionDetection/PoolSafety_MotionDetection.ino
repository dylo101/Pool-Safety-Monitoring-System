#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

Adafruit_MPU6050 mpu;

const int greenLED = 23;
const int redLED = 18;

float previousX = 0;
float previousY = 0;
float previousZ = 0;
bool firstReading = true;


void setup() {

  pinMode(greenLED, OUTPUT);
  pinMode(redLED, OUTPUT);

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

  if (motion > 0.5) {
    Serial.println("MOVING");

    digitalWrite(greenLED, HIGH);
    digitalWrite(redLED, LOW);

  } else {
    Serial.println("STILL");

    digitalWrite(greenLED, LOW);
    digitalWrite(redLED, HIGH);
  }
  delay(200);
}