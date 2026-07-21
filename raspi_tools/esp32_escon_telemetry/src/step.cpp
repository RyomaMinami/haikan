#include "step.h"
#include "config.h"

namespace stepper {

// グローバル位置カウンタ
static volatile int32_t current_position = 0;

// 内部関数: 1パルス生成
static inline void pulseOnce(uint32_t high_us, uint32_t low_us) {
  digitalWrite(PIN_STEP_CLK, HIGH);
  delayMicroseconds(high_us);
  digitalWrite(PIN_STEP_CLK, LOW);
  delayMicroseconds(low_us);
}

void setReset(bool level_high) {
  digitalWrite(PIN_STEP_RST, level_high ? HIGH : LOW);
}

void setMode(bool m0, bool m2) {
  digitalWrite(PIN_STEP_M0, m0 ? HIGH : LOW);
  digitalWrite(PIN_STEP_M2, m2 ? HIGH : LOW);
  delayMicroseconds(2);
}

void stop() {
  digitalWrite(PIN_STEP_CLK, LOW);
}

void init() {
  pinMode(PIN_STEP_CLK, OUTPUT);
  pinMode(PIN_STEP_DIR, OUTPUT);
  pinMode(PIN_STEP_RST, OUTPUT);
  pinMode(PIN_STEP_M0, OUTPUT);
  pinMode(PIN_STEP_M2, OUTPUT);

  digitalWrite(PIN_STEP_CLK, LOW);
  digitalWrite(PIN_STEP_DIR, LOW);
  digitalWrite(PIN_STEP_RST, LOW);

  // 励磁モード確定
  digitalWrite(PIN_STEP_M0, LOW);   // 固定
  digitalWrite(PIN_STEP_M2, HIGH);  // 1–2相励磁

  delayMicroseconds(100);
  
  // 位置カウンタ初期化
  current_position = 0;
}

// === パルス数制御(基本関数) ===
void runSteps(bool dir_low_is_true, uint32_t steps, uint32_t hz) {
  if (steps == 0 || hz == 0) {
    stop();
    return;
  }

  // === 方向切替前に RESET を入れる ===
  digitalWrite(PIN_STEP_RST, HIGH);   // ロジックリセット
  delayMicroseconds(5);
  digitalWrite(PIN_STEP_RST, LOW);    // 定常動作
  delayMicroseconds(5);

  // DIR設定(true -> LOW, false -> HIGH)
  digitalWrite(PIN_STEP_DIR, dir_low_is_true ? LOW : HIGH);
  delayMicroseconds(20);

  const uint32_t period_us = 1000000UL / hz;
  const uint32_t low_us =
      (period_us > STEP_HIGH_US) ? (period_us - STEP_HIGH_US) : 2;

  // 指定されたステップ数だけパルスを出力
  for (uint32_t i = 0; i < steps; ++i) {
    pulseOnce(STEP_HIGH_US, low_us);
  }

  stop();
}

// === 位置管理機能 ===

void moveRelative(int32_t steps, uint32_t hz) {
  if (steps == 0) {
    return;
  }
  
  bool dir = (steps >= 0);
  uint32_t abs_steps = (steps >= 0) ? steps : -steps;
  
  runSteps(dir, abs_steps, hz);
  
  // 位置を更新
  if (dir) {
    current_position += abs_steps;
  } else {
    current_position -= abs_steps;
  }
}

void moveAbsolute(int32_t target_position, uint32_t hz) {
  int32_t delta = target_position - current_position;
  moveRelative(delta, hz);
}

void moveTo(int32_t target, uint32_t hz) {
  moveAbsolute(target, hz);
}

int32_t getPosition() {
  return current_position;
}

void setHome() {
  current_position = 0;
}

}  // namespace stepper