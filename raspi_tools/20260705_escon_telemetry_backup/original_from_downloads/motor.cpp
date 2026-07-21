#include<motor.h>
#include<config.h>
#include <Arduino.h>

static inline int clampInt(int x, int lo, int hi) {
  if (x < lo) return lo;
  if (x > hi) return hi;
  return x;
}

void motor_init() {
  pinMode(ESCON_DIR, OUTPUT);
  pinMode(ESCON_ENABLE, OUTPUT);
  pinMode(ESCON_STOP, OUTPUT);

  ledcSetup(PWM_CH, PWM_FREQ, PWM_BITS);
  ledcAttachPin(ESCON_PWM, PWM_CH);

  // 初期：停止/無効
  digitalWrite(ESCON_STOP, LOW);
  digitalWrite(ESCON_ENABLE, LOW);
  digitalWrite(ESCON_DIR, LOW);
  ledcWrite(PWM_CH, 0);
}

void motor(int duty) {
  if (duty == 0) {
    ledcWrite(PWM_CH, 0);
    digitalWrite(ESCON_ENABLE, LOW);
    // Serial.println("停止");
    return;
  }

  // 方向
  if (duty > 0) {
    digitalWrite(ESCON_DIR, LOW);
    // Serial.println("正転");
  } else {
    digitalWrite(ESCON_DIR, HIGH);
    duty = -duty;
    // Serial.println("逆転");
  }

  duty = clampInt(duty, 0, PWM_MAX);

  digitalWrite(ESCON_ENABLE, HIGH);
  ledcWrite(PWM_CH, duty);
}
