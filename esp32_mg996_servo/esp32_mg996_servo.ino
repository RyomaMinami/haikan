/*
  ESP32 + MG996/MG996R servo test

  Wiring:
    MG996 signal  -> ESP32 GPIO26
    MG996 VCC     -> External 5-6 V servo power supply
    MG996 GND     -> External power supply GND
    ESP32 GND     -> External power supply GND

  Do not power an MG996 directly from the ESP32 3.3 V pin.
  A weak USB/5 V supply may also reset the ESP32 when the servo starts moving.
*/

#include <Arduino.h>

const int SERVO_PIN = 26;
const int PWM_CHANNEL = 0;
const int PWM_FREQ_HZ = 50;
const int PWM_RESOLUTION_BITS = 16;

// MG996/MG996R servos are commonly driven with about 500-2500 us pulses.
// If your servo hits the mechanical end stop, narrow these values, for example
// 600-2400 or 700-2300.
const int SERVO_MIN_US = 500;
const int SERVO_MAX_US = 2500;

uint32_t pulseUsToDuty(int pulseUs) {
  const uint32_t maxDuty = (1UL << PWM_RESOLUTION_BITS) - 1;
  const uint32_t periodUs = 1000000UL / PWM_FREQ_HZ;
  return (uint32_t)((uint64_t)pulseUs * maxDuty / periodUs);
}

int angleToPulseUs(int angle) {
  angle = constrain(angle, 0, 180);
  return map(angle, 0, 180, SERVO_MIN_US, SERVO_MAX_US);
}

void writeServoAngle(int angle) {
  const int pulseUs = angleToPulseUs(angle);
  ledcWrite(PWM_CHANNEL, pulseUsToDuty(pulseUs));

  Serial.print("angle=");
  Serial.print(angle);
  Serial.print(" pulse_us=");
  Serial.println(pulseUs);
}

void setup() {
  Serial.begin(115200);
  delay(300);

  ledcSetup(PWM_CHANNEL, PWM_FREQ_HZ, PWM_RESOLUTION_BITS);
  ledcAttachPin(SERVO_PIN, PWM_CHANNEL);

  Serial.println("MG996 servo test on GPIO26");
  writeServoAngle(90);
  delay(1000);
}

void loop() {
  writeServoAngle(0);
  delay(1000);

  writeServoAngle(90);
  delay(1000);

  writeServoAngle(180);
  delay(1000);

  writeServoAngle(90);
  delay(1000);
}
