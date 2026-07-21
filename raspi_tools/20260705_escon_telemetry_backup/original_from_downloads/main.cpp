/**
 * main.cpp
 * ESP2 (ESP32-WROOM-32E) — ジョイスティックによるモータ制御
 *
 * Pi からの受信形式:
 *   AXIS,ABS_Y,<val>    → DCモータ duty  (ABS_Y: 0-1023, center 512)
 *   AXIS,ABS_X,<val>    → ステッパ速度  (ABS_X: 0-1023, 左=1023, 右=0)
 *   BTN,BTN_TRIGGER,1   → 緊急停止トグル
 *
 * UART2 (Pi通信):
 *   ESP GPIO17 (TX) --> Pi ttyAMA4 RX (GPIO13 / 物理pin33)
 *   ESP GPIO16 (RX) <-- Pi ttyAMA4 TX (GPIO12 / 物理pin32)
 *
 * ⚠️ PIN_STEP_DIR=GPIO1 / PIN_STEP_M0=GPIO3 は UART0(USB Serial)と共用。
 *    DEBUG=1 にするとステッパー方向信号が乱れる可能性あり。
 *    実機運用は DEBUG=0 を推奨。
 */

#include <Arduino.h>
#include "config.h"
#include "motor.h"
#include "step.h"

// ---------- デバッグ設定 ----------
#define DEBUG 1   // 1=USB Serial ON (ステッパDIR干渉注意), 0=OFF

#if DEBUG
  #define DBG_PRINT(...)   Serial.print(__VA_ARGS__)
  #define DBG_PRINTLN(...) Serial.println(__VA_ARGS__)
  #define DBG_PRINTF(...)  Serial.printf(__VA_ARGS__)
#else
  #define DBG_PRINT(...)
  #define DBG_PRINTLN(...)
  #define DBG_PRINTF(...)
#endif

// ---------- UART2 設定 ----------
#define PI_TX_PIN  17
#define PI_RX_PIN  16
#define PI_BAUD    115200

// ---------- ABS_Y → DCモータ ----------
// 実測値: 0-1023, センター=512, 前方=0, 後方=1023
#define Y_CENTER      512
#define Y_RANGE_HALF  511   // 対称半幅
#define Y_DEAD         50   // 仮設定 — 実機で調整すること

// ---------- ABS_X → ステッパ速度 ----------
// 実測値: 0-1023, センター=512, 左=1023, 右=0 (符号反転)
#define X_CENTER      512
#define X_RANGE_HALF  511   // 対称半幅
#define X_DEAD         20   // 仮設定 — 実機で調整すること
#define STEP_HZ_MIN    200   // 最低速 [Hz]
#define STEP_HZ_MAX   2000   // 最高速 [Hz]
#define STEPS_PER_CYCLE  3   // 1ループあたりのステップ数

// ---------- 状態変数 ----------
static int  g_dcDuty  = 0;    // DCモータ duty (-255..+255)
static int  g_stepVel = 0;    // ステッパ速度 (符号付きHz, 0=停止)
static bool g_estop   = false;

// ステッパ方向追跡（同一方向連続時はRESETパルスをスキップ）
static bool s_stepLastDir   = true;
static bool s_stepDirInited = false;

// ---------- ヘルパー ----------
static int clampInt(int v, int lo, int hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

/**
 * ABS_Y (0-1023, center 512) → DCモータ duty (-255..+255)
 * 傾け度合いに比例してduty変化 (線形)
 */
static int mapYtoDuty(int raw) {
    int offset = raw - Y_CENTER;
    if (offset > -Y_DEAD && offset < Y_DEAD) return 0;

    int sign   = (offset >= 0) ? 1 : -1;
    int absOff = (offset >= 0) ? offset : -offset;
    int duty   = (int)((long)(absOff - Y_DEAD) * 255 / (Y_RANGE_HALF - Y_DEAD));
    return clampInt(sign * duty, -255, 255);
}

/**
 * ABS_X (0-1023, center 512) → ステッパ速度 (符号付きHz)
 * 左(1023) → 負, 右(0) → 正  ※符号反転
 * 傾け度合いに比例して速度変化 (線形)
 */
static int mapXtoStepVel(int raw) {
    // 1023=左(負方向) なので符号を反転: center - raw
    int offset = X_CENTER - raw;
    if (offset > -X_DEAD && offset < X_DEAD) return 0;

    int sign   = (offset >= 0) ? 1 : -1;
    int absOff = (offset >= 0) ? offset : -offset;
    int hz     = (int)((long)(absOff - X_DEAD) * (STEP_HZ_MAX - STEP_HZ_MIN)
                       / (X_RANGE_HALF - X_DEAD)) + STEP_HZ_MIN;
    return sign * clampInt(hz, STEP_HZ_MIN, STEP_HZ_MAX);
}

/**
 * ステッパを vel に従って STEPS_PER_CYCLE ステップ動かす。
 * 同一方向連続時は RESET パルスをスキップしてノイズを低減する。
 * 方向変化時のみ runSteps()（RESET パルスあり）を使用する。
 */
static void updateStepper(int vel) {
    if (vel == 0) {
        stepper::stop();
        s_stepDirInited = false;
        return;
    }

    bool     dir = (vel > 0);
    uint32_t hz  = (uint32_t)(vel >= 0 ? vel : -vel);

    if (!s_stepDirInited || dir != s_stepLastDir) {
        stepper::runSteps(dir, STEPS_PER_CYCLE, hz);
        s_stepLastDir   = dir;
        s_stepDirInited = true;
    } else {
        digitalWrite(PIN_STEP_DIR, dir ? LOW : HIGH);
        uint32_t period_us = 1000000UL / hz;
        uint32_t low_us    = (period_us > STEP_HIGH_US) ? (period_us - STEP_HIGH_US) : 2;
        for (int i = 0; i < STEPS_PER_CYCLE; i++) {
            digitalWrite(PIN_STEP_CLK, HIGH);
            delayMicroseconds(STEP_HIGH_US);
            digitalWrite(PIN_STEP_CLK, LOW);
            delayMicroseconds(low_us);
        }
    }
}

/**
 * "TYPE,NAME,VALUE\n" を解析して状態変数を更新する
 */
static void parseMessage(const String& line) {
    int c1 = line.indexOf(',');
    if (c1 < 0) return;
    int c2 = line.indexOf(',', c1 + 1);
    if (c2 < 0) return;

    String type = line.substring(0, c1);
    String name = line.substring(c1 + 1, c2);
    int    val  = line.substring(c2 + 1).toInt();

    if (type == "AXIS") {
        if (name == "ABS_Y") {
            g_dcDuty = mapYtoDuty(val);
            DBG_PRINTF("[JOY] ABS_Y=%d -> DC duty=%d\n", val, g_dcDuty);
        } else if (name == "ABS_X") {
            g_stepVel = mapXtoStepVel(val);
            DBG_PRINTF("[JOY] ABS_X=%d -> STEP vel=%d Hz\n", val, g_stepVel);
        }
    } else if (type == "BTN") {
        if (name == "BTN_TRIGGER" && val == 1) {
            g_estop = !g_estop;
            DBG_PRINTF("[JOY] ESTOP=%s\n", g_estop ? "ON" : "OFF");
        }
    }
}

// ---------- setup / loop ----------

void setup() {
#if DEBUG
    Serial.begin(115200);
    DBG_PRINTLN("[ESP2] joy_motor ready (DEBUG ON)");
#endif

    motor_init();
    stepper::init();

    Serial2.begin(PI_BAUD, SERIAL_8N1, PI_RX_PIN, PI_TX_PIN);
    DBG_PRINTF("[ESP2] UART2 ready (RX=GPIO%d TX=GPIO%d)\n", PI_RX_PIN, PI_TX_PIN);

    // ===== 仮設定: ESCONスピード変化確認テスト =====
    // duty=30 (約12%) で3秒 → duty=200 (約78%) で3秒 → 停止
    // 速度が変化すればコード・PWMは正常。変化なければESCON Studio設定を確認。
    // 確認後はこのブロックを削除すること。

    // ===== 仮設定ここまで =====
}

void loop() {
    // 1. UART受信 — バッファ内を全処理して最新値を反映
    while (Serial2.available()) {
        String line = Serial2.readStringUntil('\n');
        line.trim();
        if (line.length() > 0) {
            parseMessage(line);
        }
    }

    // 2. 緊急停止中は両モータを止めて即リターン
    if (g_estop) {
        motor(0);
        stepper::stop();
        s_stepDirInited = false;
        return;
    }

    // 3. DCモータ更新（非ブロッキング）
    motor(g_dcDuty);

    // 4. ステッパ更新（方向変化時のみRESETパルス）
    updateStepper(g_stepVel);
}
