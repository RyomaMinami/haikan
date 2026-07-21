#include "motor.h"

#include <Arduino.h>

#include "config.h"

static volatile int32_t s_encoder_counts = 0;
static volatile uint8_t s_last_ab = 0;

static int32_t s_last_counts = 0;
static uint32_t s_last_telemetry_ms = 0;
static float s_rpm = 0.0f;
static float s_encoder_rpm = 0.0f;
static float s_current_a = 0.0f;
static float s_current_v = 0.0f;
static float s_speed_v = 0.0f;
static int s_last_duty = 0;
static bool s_enabled = false;

static inline int clampInt(int x, int lo, int hi) {
  if (x < lo) return lo;
  if (x > hi) return hi;
  return x;
}

static float readAdcVoltageAverage(int pin) {
  constexpr int samples = 32;
  uint32_t mv_sum = 0;
  for (int i = 0; i < samples; i++) {
    mv_sum += analogReadMilliVolts(pin);
    delayMicroseconds(200);
  }
  return static_cast<float>(mv_sum) / static_cast<float>(samples) / 1000.0f;
}

static void IRAM_ATTR encoder_isr() {
  uint8_t a = digitalRead(ENC_A_PIN);
  uint8_t b = digitalRead(ENC_B_PIN);
  uint8_t ab = (a << 1) | b;
  uint8_t transition = (s_last_ab << 2) | ab;

  if (transition == 0b0001 || transition == 0b0111 ||
      transition == 0b1110 || transition == 0b1000) {
    s_encoder_counts++;
  } else if (transition == 0b0010 || transition == 0b1011 ||
             transition == 0b1101 || transition == 0b0100) {
    s_encoder_counts--;
  }

  s_last_ab = ab;
}

void motor_init() {
  pinMode(ESCON_DIR, OUTPUT);
  pinMode(ESCON_ENABLE, OUTPUT);
  pinMode(ESCON_STOP, OUTPUT);
  pinMode(ENC_A_PIN, INPUT);
  pinMode(ENC_B_PIN, INPUT);
  pinMode(ENC_Z_PIN, INPUT);
  pinMode(ESCON_CURRENT_ADC_PIN, INPUT);
  pinMode(ESCON_SPEED_ADC_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(ESCON_CURRENT_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(ESCON_SPEED_ADC_PIN, ADC_11db);

  ledcSetup(PWM_CH, PWM_FREQ, PWM_BITS);
  ledcAttachPin(ESCON_PWM, PWM_CH);

  digitalWrite(ESCON_STOP, LOW);
  digitalWrite(ESCON_ENABLE, LOW);
  digitalWrite(ESCON_DIR, LOW);
  ledcWrite(PWM_CH, 0);

  s_last_ab = (digitalRead(ENC_A_PIN) << 1) | digitalRead(ENC_B_PIN);
  attachInterrupt(digitalPinToInterrupt(ENC_A_PIN), encoder_isr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_B_PIN), encoder_isr, CHANGE);

  s_last_counts = 0;
  s_last_telemetry_ms = millis();
}

void motor(int duty) {
  s_last_duty = duty;

  if (duty == 0) {
    ledcWrite(PWM_CH, 0);
    digitalWrite(ESCON_ENABLE, LOW);
    s_enabled = false;
    return;
  }

  if (duty > 0) {
    digitalWrite(ESCON_DIR, LOW);
  } else {
    digitalWrite(ESCON_DIR, HIGH);
    duty = -duty;
  }

  duty = clampInt(duty, 0, PWM_MAX);

  digitalWrite(ESCON_ENABLE, HIGH);
  s_enabled = true;
  ledcWrite(PWM_CH, duty);
}

void motor_update_telemetry() {
  uint32_t now_ms = millis();
  uint32_t dt_ms = now_ms - s_last_telemetry_ms;
  if (dt_ms < MOTOR_TELEMETRY_PERIOD_MS) {
    return;
  }

  noInterrupts();
  int32_t counts = s_encoder_counts;
  interrupts();

  int32_t delta = counts - s_last_counts;
  s_last_counts = counts;
  s_last_telemetry_ms = now_ms;

  float dt_min = static_cast<float>(dt_ms) / 60000.0f;
  if (dt_min > 0.0f && ENCODER_COUNTS_PER_REV > 0.0f) {
    s_encoder_rpm = (static_cast<float>(delta) / ENCODER_COUNTS_PER_REV) / dt_min;
  }

  s_current_v = readAdcVoltageAverage(ESCON_CURRENT_ADC_PIN) *
                    ESCON_CURRENT_ADC_V_GAIN +
                ESCON_CURRENT_ADC_V_OFFSET;
  s_current_a = s_current_v * ESCON_CURRENT_A_PER_V + ESCON_CURRENT_A_OFFSET;

  s_speed_v = readAdcVoltageAverage(ESCON_SPEED_ADC_PIN);
  s_rpm = s_speed_v * ESCON_SPEED_RPM_PER_V + ESCON_SPEED_RPM_OFFSET;
}

float motor_get_rpm() {
  return s_rpm;
}

float motor_get_encoder_rpm() {
  return s_encoder_rpm;
}

float motor_get_current_a() {
  return s_current_a;
}

float motor_get_current_v() {
  return s_current_v;
}

float motor_get_speed_v() {
  return s_speed_v;
}

int motor_get_duty() {
  return s_last_duty;
}

bool motor_is_enabled() {
  return s_enabled;
}
