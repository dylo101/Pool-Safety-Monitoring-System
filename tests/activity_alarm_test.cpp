#include "../Arduino/PoolSafety_MotionDetection/ActivityAlarm.h"
#include <assert.h>
#include <stdio.h>
#include <limits>

int main() {
  ActivityAlarm alarm;
  assert(alarm.state == ActivityAlarm::DISARMED);
  alarm.sample(100, 0);
  assert(alarm.state == ActivityAlarm::DISARMED);
  assert(!alarm.acknowledge());
  assert(!alarm.setThreshold(std::numeric_limits<float>::quiet_NaN()));
  assert(!alarm.setThreshold(0.09f));
  assert(!alarm.setThreshold(20.01f));
  assert(alarm.setThreshold(1.2f));
  assert(alarm.arm(100));
  assert(!alarm.arm(101));
  assert(!alarm.setThreshold(.5f));
  alarm.sample(100, 10099);
  assert(alarm.state == ActivityAlarm::ARMING);
  assert(alarm.remaining(10099) == 1);
  alarm.sample(0, 10100);
  assert(alarm.state == ActivityAlarm::ARMED);
  alarm.sample(1.2f, 10300);  // Equality is not above the threshold.
  alarm.sample(2, 10500);     // A single spike cannot trigger an alarm.
  assert(alarm.state == ActivityAlarm::ARMED);
  for (int i = 0; i < 5; ++i) alarm.sample(0, 10700 + i * 200);
  alarm.sample(2, 11700);     // The previous spike has left the window.
  assert(alarm.state == ActivityAlarm::ARMED);
  alarm.sample(0, 11900);
  alarm.sample(2, 12100);
  assert(alarm.state == ActivityAlarm::ALERT);
  assert(!alarm.disarm());
  for (int i = 0; i < 200; ++i) alarm.sample(0, 12300 + i * 200);
  assert(alarm.state == ActivityAlarm::ALERT);
  assert(alarm.acknowledge());
  assert(alarm.state == ActivityAlarm::DISARMED);
  assert(alarm.arm(60000));
  assert(alarm.disarm());    // Settling can be canceled.
  assert(alarm.arm(UINT32_MAX - 5000));
  alarm.sample(0, 4998);
  assert(alarm.state == ActivityAlarm::ARMING);
  alarm.sample(0, 4999);
  assert(alarm.state == ActivityAlarm::ARMED);  // millis() rollover.
  alarm.sample(2, 5199);
  assert(alarm.state == ActivityAlarm::ARMED); // Old detection history was cleared.
  puts("Activity alarm rules passed");
}
