#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

Adafruit_MPU6050 mpu;

float previousX = 0;
float previousY = 0;
float previousZ = 0;
bool firstReading = true;


void setup() {

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

  float motion =
  abs(accel.acceleration.x)
  +
  abs(accel.acceleration.y)
  +
  abs(accel.acceleration.z - 9.8);

  Serial.print("Motion Score: ");
  Serial.println(motion);
  if (motion > 2.0)
  {
      Serial.println("MOVING");
  }
  else
  {
      Serial.println("STILL");
  }

  Serial.print("X: ");
  Serial.print(accel.acceleration.x);

  Serial.print("   Y: ");
  Serial.print(accel.acceleration.y);

  Serial.print("   Z: ");
  Serial.println(accel.acceleration.z);

  delay(200);
}