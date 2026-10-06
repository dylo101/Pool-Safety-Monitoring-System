#ifndef ACTIVITY_ALARM_H
#define ACTIVITY_ALARM_H

#include <stdint.h>
#include <math.h>

// Independent of Arduino so the actual firmware rules can be tested on a computer.
class ActivityAlarm {
 public:
  enum State { DISARMED, ARMING, ARMED, ALERT };
  static const uint32_t settlingMs = 10000;
  State state = DISARMED;
  float threshold = 0.5f;

  bool arm(uint32_t now) {
    if (state != DISARMED) return false;
    state = ARMING;
    armStarted = now;
    history = 0;
    return true;
  }
  bool disarm() {
    if (state == ALERT) return false;  // An alarm needs explicit acknowledgment.
    state = DISARMED;
    history = 0;
    return true;
  }
  bool acknowledge() {
    if (state != ALERT) return false;
    state = DISARMED;
    history = 0;
    return true;
  }
  bool setThreshold(float value) {
    if (state != DISARMED || !isfinite(value) || value < 0.1f || value > 20.0f) return false;
    threshold = value;
    return true;
  }
  void sample(float motion, uint32_t now) {
    if (state == ARMING) {
      if (uint32_t(now - armStarted) < settlingMs) return;
      state = ARMED;
      history = 0;
    }
    if (state != ARMED) return;
    history = ((history << 1) | (motion > threshold ? 1 : 0)) & 0x1f;
    uint8_t count = 0;
    for (uint8_t bits = history; bits; bits >>= 1) count += bits & 1;
    if (count >= 2) state = ALERT;
  }
  uint32_t remaining(uint32_t now) const {
    if (state != ARMING) return 0;
    uint32_t elapsed = now - armStarted;
    return elapsed < settlingMs ? settlingMs - elapsed : 0;
  }
  const char* name() const {
    switch (state) {
      case DISARMED: return "DISARMED";
      case ARMING: return "ARMING";
      case ARMED: return "ARMED";
      default: return "ALERT";
    }
  }

 private:
  uint32_t armStarted = 0;
  uint8_t history = 0;
};
#endif
