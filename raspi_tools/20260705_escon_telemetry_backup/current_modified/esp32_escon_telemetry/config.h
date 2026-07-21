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

// ===== ESCON telemetry =====
// Encoder count per motor shaft revolution.
// If A/B are counted on CHANGE, 4x decoding is used. Set this to the encoder
// line count * 4, or calibrate with a tachometer/ESCON Studio.
constexpr float ENCODER_COUNTS_PER_REV = 1024.0f;

// ESP32 ADC pins for ESCON Analog Monitor outputs.
// AD1: ESCON AO1 = Actual motor current.
// AD2: ESCON AO2 = Actual motor speed.
//
// Important:
// ESP32 ADC can read only 0..3.3 V. If ESCON AO2 is configured as a signed
// bipolar speed output, negative voltage is clipped/unreadable by ESP32.
// In that case, remap AO2 to 0..3.3 V in ESCON Studio or add a level shift.
constexpr int ESCON_CURRENT_ADC_PIN = 36;
constexpr int ESCON_SPEED_ADC_PIN = 39;

// ADC conversion. ESP32 ADC is noisy; calibrate against ESCON Studio.
constexpr float ESP32_ADC_REF_V = 3.3f;
constexpr float ESP32_ADC_MAX_COUNTS = 4095.0f;

// ESCON Analog Monitor scaling for actual current.
// Recommended ESCON Studio setting:
//   Analog Monitor 1 = Actual current value
//   Range 0..3.3 V or 0..3.0 V if available.
// Adjust so current_A = voltage * ESCON_CURRENT_A_PER_V + offset.
constexpr float ESCON_CURRENT_A_PER_V = 1.0f;
constexpr float ESCON_CURRENT_A_OFFSET = 0.0f;

// ESCON Analog Monitor scaling for actual speed.
// This default is valid only when ESCON AO2 is unipolar:
//   0.000 V = 0 rpm
//   3.300 V = 9690 rpm
//
// If ESCON AO2 is remapped as:
//   0.000 V = -9690 rpm
//   1.650 V = 0 rpm
//   3.300 V = +9690 rpm
// use:
//   ESCON_SPEED_RPM_PER_V = 9690.0f / 1.65f
//   ESCON_SPEED_RPM_OFFSET = -9690.0f
constexpr float ESCON_SPEED_RPM_PER_V = 9690.0f / 3.3f;
constexpr float ESCON_SPEED_RPM_OFFSET = 0.0f;

constexpr uint32_t MOTOR_TELEMETRY_PERIOD_MS = 100;

// ===== OTA update =====
// The ESP32 tries these Wi-Fi networks in order. If all fail, it starts a
// temporary access point as a fallback.
constexpr char WIFI_STA_SSID_1[] = "aokilab2";
constexpr char WIFI_STA_PASSWORD_1[] = "aokilab0118";
constexpr char WIFI_STA_SSID_2[] = "aokilab";
constexpr char WIFI_STA_PASSWORD_2[] = "aokilab0118";
constexpr char OTA_HOSTNAME[] = "esp32-escon";
constexpr char OTA_PASSWORD[] = "esconota";
constexpr char OTA_AP_SSID[] = "ESP32_ESCON_OTA";
constexpr char OTA_AP_PASSWORD[] = "esconota";
constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 8000;
