#include <Arduino.h>


constexpr int PIN_STEP_CLK = 18;  // ESP32 GPIO18 -> SLA7078 CLK
constexpr int PIN_STEP_DIR = 1;   // ESP32 GPIO3  -> SLA7078 DIR (RXD0と共用)
constexpr int PIN_STEP_RST = 14;  // ESP32 GPIO14 -> SLA7078 RESET
constexpr int PIN_STEP_M0  = 3;  // ESP32 GPIO21 -> M0
constexpr int PIN_STEP_M2  = 5;   // ESP32 GPIO5  -> M2

// 事実：あなたが波形確認できた方式を優先して High=10us を採用
constexpr uint32_t STEP_HIGH_US = 10;

// 目標周波数（まずは1kHz）
constexpr uint32_t DEFAULT_STEP_HZ = 1000;

/* 使うピンの定義 */
constexpr int ESCON_DIR    = 23;
constexpr int ESCON_ENABLE = 22;
constexpr int ESCON_STOP   = 19;
constexpr int ESCON_PWM    = 21;

// ===== LEDC(PWM) =====
constexpr int PWM_CH   = 0;
constexpr int PWM_BITS = 8;
// constexpr int PWM_FREQ = 490;    // Hz
// constexpr int PWM_FREQ = 200;    // Hz → 動作せず
// constexpr int PWM_FREQ = 1000;   // Hz → 動作・速度固定
constexpr int PWM_FREQ = 20000;  // Hz → 動作・速度固定（基準に戻す）

constexpr int PWM_MAX = (1 << PWM_BITS) - 1;


//エンコーダ

constexpr gpio_num_t ENC_A_PIN = GPIO_NUM_34; // OUT2A_3.3
constexpr gpio_num_t ENC_B_PIN = GPIO_NUM_35; // OUT2B_3.3
constexpr int        ENC_Z_PIN = 32;          // OUT2Z_3.3

constexpr int Z_EDGE = RISING;
// 表示周期 [ms]
constexpr uint32_t PRINT_PERIOD_MS = 200;

// 1回転（Z→Z）測定をするか
constexpr bool MEASURE_CPR_BY_Z = true;
