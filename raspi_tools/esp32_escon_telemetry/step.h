#ifndef STEP_H
#define STEP_H

#include <stdint.h>

namespace stepper {

// 初期化
void init();

// リセット制御
void setReset(bool level_high);

// モード設定
void setMode(bool m0, bool m2);

// 停止
void stop();

// === パルス数制御 ===
// 指定したステップ数だけ回転
void runSteps(bool dir_low_is_true, uint32_t steps, uint32_t hz);

// === 位置管理機能 ===
// 相対移動(現在位置からの移動)
void moveRelative(int32_t steps, uint32_t hz);

// 絶対位置への移動
void moveAbsolute(int32_t target_position, uint32_t hz);

// 現在位置を取得
int32_t getPosition();

// 原点設定(現在位置を0にリセット)
void setHome();

// 指定位置まで移動(方向自動判定)
void moveTo(int32_t target, uint32_t hz);

}  // namespace stepper

#endif  // STEP_H