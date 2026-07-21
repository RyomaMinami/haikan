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

// Continuous step clock for the SLA7078MPRT.
// ESCON PWM uses PWM_CH=0, so keep the step clock on another LEDC channel.
constexpr int STEP_PWM_CH = 1;
constexpr int STEP_PWM_BITS = 8;
constexpr int STEP_PWM_DUTY = 128;


//エンコーダ

constexpr gpio_num_t ENC_A_PIN = GPIO_NUM_34; // OUT2A_3.3
constexpr gpio_num_t ENC_B_PIN = GPIO_NUM_35; // OUT2B_3.3
constexpr int        ENC_Z_PIN = 32;          // OUT2Z_3.3.

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
constexpr float ESCON_CURRENT_ADC_V_GAIN = 1.0892f;  // 2026-07-07: ESP reads 1.5149 V when ESCON AO1 is 1.650 V.
constexpr float ESCON_CURRENT_ADC_V_OFFSET = 0.0f;

// ESCON Analog Monitor scaling for actual current.
// Current ESCON Studio setting:
//   Analog Monitor 1 = Actual current value
//   0.000 V = -5 A
//   1.650 V =  0 A
//   3.300 V = +5 A
// Adjust so current_A = voltage * ESCON_CURRENT_A_PER_V + offset.
constexpr float ESCON_CURRENT_A_PER_V = 10.0f / 3.3f;
constexpr float ESCON_CURRENT_A_OFFSET = -5.0f;

// ESCON Analog Monitor scaling for actual speed.
// Target ESCON AO2 setting:
//   0.000 V = -9690 rpm
//   1.650 V = 0 rpm
//   3.300 V = +9690 rpm
//
// This makes the signed speed readable by ESP32's 0..3.3 V ADC.
// If ESCON AO2 is still configured as bipolar +/- voltage, ESP32 cannot read
// negative speed. Fix the ESCON AO2 setting before trusting rpm.
constexpr float ESCON_SPEED_RPM_PER_V = 9690.0f / 1.65f;
constexpr float ESCON_SPEED_RPM_OFFSET = -9690.0f;

// ===== Stepper current telemetry =====
// SLA7078MPRT SenseA/SenseB are filtered externally, then read by ESP32.
//
// Wiring actually mounted on the current robot:
//   SenseA -> 1 kOhm -> GPIO27, 0.33 uF from GPIO27 to GND
//   SenseB -> 1 kOhm -> GPIO32, 0.33 uF from GPIO32 to GND
//
// GPIO27 is ADC2. It is unreliable while Wi-Fi is active, so this firmware
// turns Wi-Fi off after the OTA window. Wait about 30 s after reset before
// trusting step_sense_a_v.
//
// GPIO32 is also ENC_Z_PIN / OUT2Z. While this wiring is used, do not trust
// encoder Z telemetry at the same time as stepper current telemetry.
constexpr int STEP_SENSE_A_ADC_PIN = 27;
constexpr int STEP_SENSE_B_ADC_PIN = 32;

// SLA7078MPRT has an internal current-sense resistor of 0.155 ohm typ.
// Phase current estimate:
//   phase_current_A = Sense_voltage_V / 0.155 ohm
//
// Treat this as an estimate because the Sense pin is a PWM/chopper waveform and
// the external RC filter logs an averaged voltage, not a synchronized peak.
constexpr float STEP_SENSE_RESISTOR_OHM = 0.155f;
constexpr float STEP_SENSE_ADC_V_GAIN = 1.0f;
constexpr float STEP_SENSE_ADC_V_OFFSET = 0.0f;

constexpr uint32_t MOTOR_TELEMETRY_PERIOD_MS = 100;

// ===== Body accelerometer telemetry =====
// AE-KXR94-2050 connected to the main board ACCE1 header.
//
// Schematic note:
//   ACCE1_X -> ESP32 GPIO33
//   ACCE1_Y -> ESP32 GPIO25
//   ACCE1_Z -> ESP32 GPIO26
//
// Important:
// GPIO25/26 are ADC2 pins on ESP32. ADC2 can be unavailable or noisy while
// Wi-Fi is active for OTA. If Y/Z are unstable during Wi-Fi operation, move the
// accelerometer axes to ADC1 pins or disable Wi-Fi after boot.
constexpr int ACCE1_X_ADC_PIN = 33;
constexpr int ACCE1_Y_ADC_PIN = 25;
constexpr int ACCE1_Z_ADC_PIN = 26;

// AE-KXR94-2050 nominal values when powered from 3.3 V.
// Calibrate these from measured stationary voltages if the mounting direction
// or sensor board differs.
constexpr float ACCE1_ZERO_G_V = 1.65f;
constexpr float ACCE1_SENSITIVITY_V_PER_G = 0.66f;

// ===== Solenoid valve outputs =====
// Commands:
//   VALVE,MOVE_PUSH,1      keep mobile cylinder push valve ON
//   VALVE,MOVE_PUSH,0      turn it OFF
//   VALVE,ALL,0            turn every valve OFF
//   VALVE,STATUS
//
// Safety note:
// Every valve command is a dead-man command.  When ON commands stop arriving,
// the firmware turns the valve OFF after VALVE_HOLD_TIMEOUT_MS.  Use the
// Raspberry Pi bridge to repeat ON commands while a controller button is held.
//
// The EAGLE files show solenoid driver outputs, but the exact ESP32 GPIO
// mapping of this machine still needs confirmation.  Leave pins as -1 until the
// wiring is verified; commands to unassigned valves are rejected and no GPIO is
// toggled.
constexpr int PIN_VALVE_MOVE_PUSH = -1;      // SY3320-5LZ-C4 mobile push
constexpr int PIN_VALVE_MOVE_PULL = -1;      // SY3320-5LZ-C4 mobile retract
constexpr int PIN_VALVE_DRILL_PUSH = -1;     // SY3320-5LZ-C4 drilling push
constexpr int PIN_VALVE_DRILL_PULL = -1;     // SY3320-5LZ-C4 drilling retract
constexpr int PIN_VALVE_GRINDER_AIR = -1;    // VXZ232 grinder air ON/OFF
constexpr bool VALVE_ACTIVE_HIGH = true;
constexpr uint32_t VALVE_HOLD_TIMEOUT_MS = 350;

// ===== Hole expansion motion =====
// Command example from Raspberry Pi / PC:
//   EXPAND,START,dc=70,feed_steps=400,feed_hz=500,dwell_ms=300,passes=1
//   EXPAND,STOP
//
// The firmware keeps the grinder/DC motor spinning, feeds the stepper outward,
// dwells briefly, then retracts. Values are intentionally conservative; tune
// them from the MATLAB calculation and the actual mechanism.
constexpr int EXPAND_DEFAULT_DC_DUTY = 70;
constexpr uint32_t EXPAND_DEFAULT_FEED_STEPS = 400;
constexpr uint32_t EXPAND_DEFAULT_RETRACT_STEPS = 400;
constexpr uint32_t EXPAND_DEFAULT_FEED_HZ = 500;
constexpr uint32_t EXPAND_DEFAULT_SPINUP_MS = 1000;
constexpr uint32_t EXPAND_DEFAULT_DWELL_MS = 300;
constexpr uint32_t EXPAND_DEFAULT_PASS_PAUSE_MS = 200;
constexpr uint8_t EXPAND_DEFAULT_PASSES = 1;

constexpr int EXPAND_MAX_DC_DUTY = 140;
constexpr uint32_t EXPAND_MAX_STEPS_PER_MOVE = 20000;
constexpr uint32_t EXPAND_MAX_FEED_HZ = 1500;
constexpr uint8_t EXPAND_MAX_PASSES = 20;

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

// Keep Wi-Fi/OTA available briefly after boot, then turn Wi-Fi off so ADC2
// pins such as ACCE1_Y/Z can be read normally. Reset the ESP32 to open this OTA
// window again.
constexpr uint32_t OTA_ACTIVE_WINDOW_MS = 30000;
