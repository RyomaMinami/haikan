#include <Arduino.h>
#include <ArduinoOTA.h>
#include <WiFi.h>

#include "config.h"
#include "motor.h"
#include "step.h"

// Set these to 1 only for USB checks. GPIO1/GPIO3 are shared with stepper
// signals in the current wiring, so keep them 0 for the actual machine.
#ifndef DEBUG
#define DEBUG 0
#endif
#ifndef USB_TELEMETRY
#define USB_TELEMETRY 0
#endif
#ifndef ENABLE_OTA
#define ENABLE_OTA 1
#endif

#if DEBUG
#define DBG_PRINTF(...) Serial.printf(__VA_ARGS__)
#define DBG_PRINTLN(...) Serial.println(__VA_ARGS__)
#else
#define DBG_PRINTF(...)
#define DBG_PRINTLN(...)
#endif

#define PI_TX_PIN 17
#define PI_RX_PIN 16
#define PI_BAUD 115200

#define Y_CENTER 512
#define Y_RANGE_HALF 511
#define Y_DEAD 50

#define X_CENTER 512
#define X_RANGE_HALF 511
#define X_DEAD 20
#define STEP_HZ_MIN 200
#define STEP_HZ_MAX 2000
#define STEPS_PER_CYCLE 3

static int g_dcDuty = 0;
static int g_stepVel = 0;
static bool g_estop = false;

static bool s_stepLastDir = true;
static bool s_stepDirInited = false;
static uint32_t s_lastTelemetryMs = 0;

#if ENABLE_OTA
static bool s_otaReady = false;
static const char* s_connectedSsid = "";
#endif

static int clampInt(int v, int lo, int hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

static int mapYtoDuty(int raw) {
  int offset = raw - Y_CENTER;
  if (offset > -Y_DEAD && offset < Y_DEAD) return 0;

  int sign = (offset >= 0) ? 1 : -1;
  int absOff = (offset >= 0) ? offset : -offset;
  int duty = static_cast<int>(
      static_cast<long>(absOff - Y_DEAD) * 255 / (Y_RANGE_HALF - Y_DEAD));
  return clampInt(sign * duty, -255, 255);
}

static int mapXtoStepVel(int raw) {
  int offset = X_CENTER - raw;
  if (offset > -X_DEAD && offset < X_DEAD) return 0;

  int sign = (offset >= 0) ? 1 : -1;
  int absOff = (offset >= 0) ? offset : -offset;
  int hz = static_cast<int>(
               static_cast<long>(absOff - X_DEAD) * (STEP_HZ_MAX - STEP_HZ_MIN) /
               (X_RANGE_HALF - X_DEAD)) +
           STEP_HZ_MIN;
  return sign * clampInt(hz, STEP_HZ_MIN, STEP_HZ_MAX);
}

static void updateStepper(int vel) {
  if (vel == 0) {
    stepper::stop();
    s_stepDirInited = false;
    return;
  }

  bool dir = (vel > 0);
  uint32_t hz = static_cast<uint32_t>(vel >= 0 ? vel : -vel);

  if (!s_stepDirInited || dir != s_stepLastDir) {
    stepper::runSteps(dir, STEPS_PER_CYCLE, hz);
    s_stepLastDir = dir;
    s_stepDirInited = true;
  } else {
    digitalWrite(PIN_STEP_DIR, dir ? LOW : HIGH);
    uint32_t period_us = 1000000UL / hz;
    uint32_t low_us = (period_us > STEP_HIGH_US) ? (period_us - STEP_HIGH_US) : 2;
    for (int i = 0; i < STEPS_PER_CYCLE; i++) {
      digitalWrite(PIN_STEP_CLK, HIGH);
      delayMicroseconds(STEP_HIGH_US);
      digitalWrite(PIN_STEP_CLK, LOW);
      delayMicroseconds(low_us);
    }
  }
}

static void sendTelemetry(bool force = false) {
  uint32_t now = millis();
  if (!force && now - s_lastTelemetryMs < MOTOR_TELEMETRY_PERIOD_MS) {
    return;
  }
  s_lastTelemetryMs = now;

  char line[192];
  snprintf(
      line,
      sizeof(line),
      "TEL,ms=%lu,motor_id=1,duty=%d,enabled=%d,rpm=%.3f,current_a=%.4f,"
      "current_v=%.4f,speed_v=%.4f,encoder_rpm=%.3f,step_hz=%d,estop=%d,"
      "state=%s\n",
      static_cast<unsigned long>(now),
      motor_get_duty(),
      motor_is_enabled() ? 1 : 0,
      motor_get_rpm(),
      motor_get_current_a(),
      motor_get_current_v(),
      motor_get_speed_v(),
      motor_get_encoder_rpm(),
      g_stepVel,
      g_estop ? 1 : 0,
      g_estop ? "estop" : (motor_is_enabled() ? "move" : "stop"));
  Serial2.print(line);
#if USB_TELEMETRY
  Serial.print(line);
#endif
}

#if ENABLE_OTA
static bool connectWifiCandidate(const char* ssid, const char* password) {
  if (strlen(ssid) == 0) {
    return false;
  }
  WiFi.begin(ssid, password);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - start < WIFI_CONNECT_TIMEOUT_MS) {
    delay(100);
  }
  if (WiFi.status() == WL_CONNECTED) {
    s_connectedSsid = ssid;
    return true;
  }
  WiFi.disconnect(true);
  delay(200);
  return false;
}

static void setupOta() {
  WiFi.mode(WIFI_STA);

  connectWifiCandidate(WIFI_STA_SSID_1, WIFI_STA_PASSWORD_1) ||
      connectWifiCandidate(WIFI_STA_SSID_2, WIFI_STA_PASSWORD_2);

  if (WiFi.status() != WL_CONNECTED) {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(OTA_AP_SSID, OTA_AP_PASSWORD);
  }

  ArduinoOTA.setHostname(OTA_HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.onStart([]() {
    g_estop = true;
    g_dcDuty = 0;
    g_stepVel = 0;
    motor(0);
    stepper::stop();
    Serial2.println("OTA,start");
  });
  ArduinoOTA.onEnd([]() {
    Serial2.println("OTA,end");
  });
  ArduinoOTA.onError([](ota_error_t error) {
    Serial2.printf("OTA,error=%u\n", static_cast<unsigned>(error));
  });
  ArduinoOTA.begin();
  s_otaReady = true;

  if (WiFi.getMode() == WIFI_AP) {
    Serial2.printf("OTA,ap_ssid=%s,ip=%s\n", OTA_AP_SSID,
                   WiFi.softAPIP().toString().c_str());
  } else {
    Serial2.printf("OTA,ssid=%s,ip=%s\n", s_connectedSsid,
                   WiFi.localIP().toString().c_str());
  }
}
#endif

static void parseMessage(const String& line) {
  if (line == "ping") {
    Serial2.println("pong");
    sendTelemetry(true);
    return;
  }

  int c1 = line.indexOf(',');
  if (c1 < 0) return;
  int c2 = line.indexOf(',', c1 + 1);
  if (c2 < 0) return;

  String type = line.substring(0, c1);
  String name = line.substring(c1 + 1, c2);
  int val = line.substring(c2 + 1).toInt();

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

void setup() {
#if DEBUG || USB_TELEMETRY
  Serial.begin(115200);
#endif
#if DEBUG
  DBG_PRINTLN("[ESP2] escon telemetry firmware");
#endif

  motor_init();
#if !USB_TELEMETRY
  stepper::init();
#endif

  Serial2.begin(PI_BAUD, SERIAL_8N1, PI_RX_PIN, PI_TX_PIN);
  Serial2.println("BOOT,esp32_escon_telemetry");
#if USB_TELEMETRY
  Serial.println("BOOT,esp32_escon_telemetry_usb");
#endif
#if ENABLE_OTA
  setupOta();
#endif
  sendTelemetry(true);
}

void loop() {
#if ENABLE_OTA
  if (s_otaReady) {
    ArduinoOTA.handle();
  }
#endif

  while (Serial2.available()) {
    String line = Serial2.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      parseMessage(line);
    }
  }

  if (g_estop) {
    motor(0);
#if !USB_TELEMETRY
    stepper::stop();
#endif
    s_stepDirInited = false;
  } else {
    motor(g_dcDuty);
#if !USB_TELEMETRY
    updateStepper(g_stepVel);
#endif
  }

  motor_update_telemetry();
  sendTelemetry();
}
