#include <Arduino.h>
#include <ArduinoOTA.h>
#include <WiFi.h>
#include <math.h>

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

enum class RunMode {
  Manual,
  Expand,
};

enum class ExpandState {
  Idle,
  Spinup,
  FeedOut,
  Dwell,
  Retract,
  PassPause,
  Done,
  Aborted,
};

struct ExpandPlan {
  int dcDuty = EXPAND_DEFAULT_DC_DUTY;
  uint32_t feedSteps = EXPAND_DEFAULT_FEED_STEPS;
  uint32_t retractSteps = EXPAND_DEFAULT_RETRACT_STEPS;
  uint32_t feedHz = EXPAND_DEFAULT_FEED_HZ;
  uint32_t spinupMs = EXPAND_DEFAULT_SPINUP_MS;
  uint32_t dwellMs = EXPAND_DEFAULT_DWELL_MS;
  uint32_t passPauseMs = EXPAND_DEFAULT_PASS_PAUSE_MS;
  uint8_t passes = EXPAND_DEFAULT_PASSES;
};

struct TimedStepMove {
  bool active = false;
  int32_t steps = 0;
  uint32_t hz = 0;
  uint32_t startMs = 0;
  uint32_t durationMs = 0;
};

static RunMode s_runMode = RunMode::Manual;
static ExpandState s_expandState = ExpandState::Idle;
static ExpandPlan s_expandPlan;
static TimedStepMove s_timedStepMove;
static uint32_t s_expandStateStartMs = 0;
static uint8_t s_expandPassIndex = 0;
static int32_t s_expandEstimatedStep = 0;

static bool s_stepLastDir = true;
static bool s_stepDirInited = false;
static bool s_stepClockRunning = false;
static uint32_t s_stepLastHz = 0;
static uint32_t s_lastTelemetryMs = 0;

enum ValveIndex : uint8_t {
  VALVE_MOVE_PUSH = 0,
  VALVE_MOVE_PULL,
  VALVE_DRILL_PUSH,
  VALVE_DRILL_PULL,
  VALVE_GRINDER_AIR,
  VALVE_COUNT,
};

struct ValveChannel {
  const char* commandName;
  const char* telemetryName;
  int pin;
  bool on;
  uint32_t lastOnCommandMs;
};

static ValveChannel s_valves[VALVE_COUNT] = {
    {"MOVE_PUSH", "move_push", PIN_VALVE_MOVE_PUSH, false, 0},
    {"MOVE_PULL", "move_pull", PIN_VALVE_MOVE_PULL, false, 0},
    {"DRILL_PUSH", "drill_push", PIN_VALVE_DRILL_PUSH, false, 0},
    {"DRILL_PULL", "drill_pull", PIN_VALVE_DRILL_PULL, false, 0},
    {"GRINDER_AIR", "grinder_air", PIN_VALVE_GRINDER_AIR, false, 0},
};

#if ENABLE_OTA
static bool s_otaReady = false;
static const char* s_connectedSsid = "";
#endif

static int clampInt(int v, int lo, int hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

static uint32_t clampU32(uint32_t v, uint32_t lo, uint32_t hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

static const char* runModeName() {
  return (s_runMode == RunMode::Expand) ? "expand" : "manual";
}

static const char* expandStateName() {
  switch (s_expandState) {
    case ExpandState::Idle:
      return "idle";
    case ExpandState::Spinup:
      return "spinup";
    case ExpandState::FeedOut:
      return "feed_out";
    case ExpandState::Dwell:
      return "dwell";
    case ExpandState::Retract:
      return "retract";
    case ExpandState::PassPause:
      return "pass_pause";
    case ExpandState::Done:
      return "done";
    case ExpandState::Aborted:
      return "aborted";
  }
  return "unknown";
}

static bool valvePinConfigured(int pin) {
  return pin >= 0;
}

static void writeValvePin(const ValveChannel& valve, bool on) {
  if (!valvePinConfigured(valve.pin)) {
    return;
  }
  const int level = (on == VALVE_ACTIVE_HIGH) ? HIGH : LOW;
  digitalWrite(valve.pin, level);
}

static void turnValveOff(ValveIndex index) {
  ValveChannel& valve = s_valves[index];
  valve.on = false;
  valve.lastOnCommandMs = 0;
  writeValvePin(valve, false);
}

static void turnAllValvesOff() {
  for (uint8_t i = 0; i < VALVE_COUNT; i++) {
    turnValveOff(static_cast<ValveIndex>(i));
  }
}

static void enforceValveInterlock(ValveIndex index) {
  if (index == VALVE_MOVE_PUSH) {
    turnValveOff(VALVE_MOVE_PULL);
  } else if (index == VALVE_MOVE_PULL) {
    turnValveOff(VALVE_MOVE_PUSH);
  } else if (index == VALVE_DRILL_PUSH) {
    turnValveOff(VALVE_DRILL_PULL);
  } else if (index == VALVE_DRILL_PULL) {
    turnValveOff(VALVE_DRILL_PUSH);
  }
}

static int findValveIndex(const String& name) {
  for (uint8_t i = 0; i < VALVE_COUNT; i++) {
    if (name.equalsIgnoreCase(s_valves[i].commandName) ||
        name.equalsIgnoreCase(s_valves[i].telemetryName)) {
      return i;
    }
  }
  return -1;
}

static uint8_t valveMask() {
  uint8_t mask = 0;
  for (uint8_t i = 0; i < VALVE_COUNT; i++) {
    if (s_valves[i].on) {
      mask |= (1U << i);
    }
  }
  return mask;
}

static void setValve(ValveIndex index, bool on) {
  ValveChannel& valve = s_valves[index];
  if (on) {
    if (!valvePinConfigured(valve.pin)) {
      Serial2.printf("VALVE,rejected,name=%s,reason=pin_unset\n",
                     valve.commandName);
      return;
    }
    if (g_estop) {
      Serial2.printf("VALVE,rejected,name=%s,reason=estop\n",
                     valve.commandName);
      return;
    }
    enforceValveInterlock(index);
    valve.on = true;
    valve.lastOnCommandMs = millis();
    writeValvePin(valve, true);
  } else {
    turnValveOff(index);
  }

  Serial2.printf("VALVE,set,name=%s,on=%d,pin=%d\n",
                 valve.commandName,
                 valve.on ? 1 : 0,
                 valve.pin);
}

static void initValves() {
  for (uint8_t i = 0; i < VALVE_COUNT; i++) {
    ValveChannel& valve = s_valves[i];
    if (valvePinConfigured(valve.pin)) {
      writeValvePin(valve, false);
      pinMode(valve.pin, OUTPUT);
      writeValvePin(valve, false);
    }
  }
}

static void updateValves() {
  if (g_estop) {
    turnAllValvesOff();
    return;
  }

  const uint32_t now = millis();
  for (uint8_t i = 0; i < VALVE_COUNT; i++) {
    ValveChannel& valve = s_valves[i];
    if (valve.on && now - valve.lastOnCommandMs > VALVE_HOLD_TIMEOUT_MS) {
      turnValveOff(static_cast<ValveIndex>(i));
      Serial2.printf("VALVE,timeout,name=%s\n", valve.commandName);
    }
  }
}

static float readAdcVoltage(int pin) {
  constexpr int samples = 8;
  uint32_t mv_sum = 0;
  for (int i = 0; i < samples; i++) {
    mv_sum += analogReadMilliVolts(pin);
    delayMicroseconds(100);
  }
  return static_cast<float>(mv_sum) / static_cast<float>(samples) / 1000.0f;
}

static float readStepSenseVoltage(int pin) {
  return readAdcVoltage(pin) * STEP_SENSE_ADC_V_GAIN + STEP_SENSE_ADC_V_OFFSET;
}

static float stepSenseVoltageToCurrent(float voltage) {
  if (STEP_SENSE_RESISTOR_OHM <= 0.0f) {
    return 0.0f;
  }
  return voltage / STEP_SENSE_RESISTOR_OHM;
}

static float accelVoltageToG(float voltage) {
  return (voltage - ACCE1_ZERO_G_V) / ACCE1_SENSITIVITY_V_PER_G;
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

static void stopStepClock() {
  if (s_stepClockRunning) {
    ledcWrite(STEP_PWM_CH, 0);
    ledcDetachPin(PIN_STEP_CLK);
    digitalWrite(PIN_STEP_CLK, LOW);
    s_stepClockRunning = false;
    s_stepLastHz = 0;
  }
  stepper::stop();
  s_stepDirInited = false;
}

static void updateStepper(int vel) {
  if (vel == 0) {
    stopStepClock();
    return;
  }

  bool dir = (vel > 0);
  uint32_t hz = static_cast<uint32_t>(vel >= 0 ? vel : -vel);

  if (!s_stepDirInited || dir != s_stepLastDir) {
    if (s_stepClockRunning) {
      ledcWrite(STEP_PWM_CH, 0);
      ledcDetachPin(PIN_STEP_CLK);
      s_stepClockRunning = false;
    }
    digitalWrite(PIN_STEP_DIR, dir ? LOW : HIGH);
    delayMicroseconds(20);
    s_stepLastDir = dir;
    s_stepDirInited = true;
    s_stepLastHz = 0;
  }

  if (!s_stepClockRunning || hz != s_stepLastHz) {
    ledcSetup(STEP_PWM_CH, hz, STEP_PWM_BITS);
    ledcAttachPin(PIN_STEP_CLK, STEP_PWM_CH);
    ledcWrite(STEP_PWM_CH, STEP_PWM_DUTY);
    s_stepClockRunning = true;
    s_stepLastHz = hz;
  }
}

static void startTimedStepMove(int32_t steps, uint32_t hz) {
  if (steps == 0 || hz == 0) {
    s_timedStepMove.active = false;
    g_stepVel = 0;
    stopStepClock();
    return;
  }

  uint32_t absSteps = static_cast<uint32_t>(steps > 0 ? steps : -steps);
  hz = clampU32(hz, 1, EXPAND_MAX_FEED_HZ);

  s_timedStepMove.active = true;
  s_timedStepMove.steps = steps;
  s_timedStepMove.hz = hz;
  s_timedStepMove.startMs = millis();
  s_timedStepMove.durationMs =
      static_cast<uint32_t>((static_cast<uint64_t>(absSteps) * 1000ULL +
                             static_cast<uint64_t>(hz) - 1ULL) /
                            static_cast<uint64_t>(hz));

  g_stepVel = (steps > 0) ? static_cast<int>(hz) : -static_cast<int>(hz);
  updateStepper(g_stepVel);
}

static bool updateTimedStepMove() {
  if (!s_timedStepMove.active) {
    return true;
  }

  if (millis() - s_timedStepMove.startMs >= s_timedStepMove.durationMs) {
    s_expandEstimatedStep += s_timedStepMove.steps;
    s_timedStepMove.active = false;
    g_stepVel = 0;
    stopStepClock();
    return true;
  }

  return false;
}

static void setExpandState(ExpandState state) {
  s_expandState = state;
  s_expandStateStartMs = millis();
}

static void abortExpansion(const char* reason) {
  motor(0);
  g_dcDuty = 0;
  g_stepVel = 0;
  stopStepClock();
  s_timedStepMove.active = false;
  s_runMode = RunMode::Manual;
  setExpandState(ExpandState::Aborted);
  Serial2.printf("EXPAND,aborted,reason=%s\n", reason);
}

static void finishExpansion() {
  motor(0);
  g_dcDuty = 0;
  g_stepVel = 0;
  stopStepClock();
  s_timedStepMove.active = false;
  s_runMode = RunMode::Manual;
  setExpandState(ExpandState::Done);
  Serial2.printf("EXPAND,done,passes=%u,estimated_step=%ld\n",
                 static_cast<unsigned>(s_expandPassIndex),
                 static_cast<long>(s_expandEstimatedStep));
}

static void startExpansion(const ExpandPlan& plan) {
  if (g_estop) {
    Serial2.println("EXPAND,rejected,reason=estop");
    return;
  }

  s_expandPlan = plan;
  s_expandPlan.dcDuty =
      clampInt(s_expandPlan.dcDuty, -EXPAND_MAX_DC_DUTY, EXPAND_MAX_DC_DUTY);
  s_expandPlan.feedSteps =
      clampU32(s_expandPlan.feedSteps, 1, EXPAND_MAX_STEPS_PER_MOVE);
  s_expandPlan.retractSteps =
      clampU32(s_expandPlan.retractSteps, 0, EXPAND_MAX_STEPS_PER_MOVE);
  s_expandPlan.feedHz = clampU32(s_expandPlan.feedHz, 1, EXPAND_MAX_FEED_HZ);
  if (s_expandPlan.passes == 0) {
    s_expandPlan.passes = 1;
  }
  if (s_expandPlan.passes > EXPAND_MAX_PASSES) {
    s_expandPlan.passes = EXPAND_MAX_PASSES;
  }

  s_runMode = RunMode::Expand;
  s_expandPassIndex = 0;
  s_timedStepMove.active = false;
  g_dcDuty = s_expandPlan.dcDuty;
  g_stepVel = 0;
  motor(g_dcDuty);
  stopStepClock();
  setExpandState(ExpandState::Spinup);

  Serial2.printf(
      "EXPAND,start,dc=%d,feed_steps=%lu,retract_steps=%lu,feed_hz=%lu,"
      "spinup_ms=%lu,dwell_ms=%lu,passes=%u\n",
      s_expandPlan.dcDuty,
      static_cast<unsigned long>(s_expandPlan.feedSteps),
      static_cast<unsigned long>(s_expandPlan.retractSteps),
      static_cast<unsigned long>(s_expandPlan.feedHz),
      static_cast<unsigned long>(s_expandPlan.spinupMs),
      static_cast<unsigned long>(s_expandPlan.dwellMs),
      static_cast<unsigned>(s_expandPlan.passes));
}

static int readIntParam(const String& line, const char* key, int fallback) {
  String prefix = String(key) + "=";
  int start = line.indexOf(prefix);
  if (start < 0) {
    return fallback;
  }
  start += prefix.length();
  int end = line.indexOf(',', start);
  if (end < 0) {
    end = line.length();
  }
  return line.substring(start, end).toInt();
}

static bool parseOnOffToken(const String& token, bool* out) {
  if (token == "1" || token.equalsIgnoreCase("ON") ||
      token.equalsIgnoreCase("HIGH") || token.equalsIgnoreCase("TRUE")) {
    *out = true;
    return true;
  }
  if (token == "0" || token.equalsIgnoreCase("OFF") ||
      token.equalsIgnoreCase("LOW") || token.equalsIgnoreCase("FALSE")) {
    *out = false;
    return true;
  }
  return false;
}

static void printValveStatus() {
  Serial2.printf(
      "VALVE,status,mask=%u,move_push=%d,move_pull=%d,drill_push=%d,"
      "drill_pull=%d,grinder_air=%d,pins=%d/%d/%d/%d/%d\n",
      static_cast<unsigned>(valveMask()),
      s_valves[VALVE_MOVE_PUSH].on ? 1 : 0,
      s_valves[VALVE_MOVE_PULL].on ? 1 : 0,
      s_valves[VALVE_DRILL_PUSH].on ? 1 : 0,
      s_valves[VALVE_DRILL_PULL].on ? 1 : 0,
      s_valves[VALVE_GRINDER_AIR].on ? 1 : 0,
      s_valves[VALVE_MOVE_PUSH].pin,
      s_valves[VALVE_MOVE_PULL].pin,
      s_valves[VALVE_DRILL_PUSH].pin,
      s_valves[VALVE_DRILL_PULL].pin,
      s_valves[VALVE_GRINDER_AIR].pin);
}

static void handleValveCommand(const String& line) {
  if (line.startsWith("VALVE,STATUS")) {
    printValveStatus();
    return;
  }

  int c1 = line.indexOf(',');
  int c2 = line.indexOf(',', c1 + 1);
  if (c1 < 0 || c2 < 0) {
    Serial2.println("VALVE,rejected,reason=bad_command");
    return;
  }

  String name = line.substring(c1 + 1, c2);
  String value = line.substring(c2 + 1);
  name.trim();
  value.trim();

  bool on = false;
  if (!parseOnOffToken(value, &on)) {
    Serial2.println("VALVE,rejected,reason=bad_value");
    return;
  }

  if (name.equalsIgnoreCase("ALL")) {
    if (on) {
      Serial2.println("VALVE,rejected,name=ALL,reason=all_on_not_allowed");
    } else {
      turnAllValvesOff();
      Serial2.println("VALVE,set,name=ALL,on=0");
    }
    return;
  }

  int index = findValveIndex(name);
  if (index < 0) {
    Serial2.printf("VALVE,rejected,name=%s,reason=unknown\n", name.c_str());
    return;
  }

  setValve(static_cast<ValveIndex>(index), on);
}

static void updateExpansion() {
  if (s_runMode != RunMode::Expand) {
    return;
  }

  if (g_estop) {
    abortExpansion("estop");
    return;
  }

  motor(s_expandPlan.dcDuty);

  switch (s_expandState) {
    case ExpandState::Spinup:
      if (millis() - s_expandStateStartMs >= s_expandPlan.spinupMs) {
        s_expandPassIndex++;
        startTimedStepMove(static_cast<int32_t>(s_expandPlan.feedSteps),
                           s_expandPlan.feedHz);
        setExpandState(ExpandState::FeedOut);
      }
      break;

    case ExpandState::FeedOut:
      if (updateTimedStepMove()) {
        setExpandState(ExpandState::Dwell);
      }
      break;

    case ExpandState::Dwell:
      if (millis() - s_expandStateStartMs >= s_expandPlan.dwellMs) {
        if (s_expandPlan.retractSteps > 0) {
          startTimedStepMove(-static_cast<int32_t>(s_expandPlan.retractSteps),
                             s_expandPlan.feedHz);
          setExpandState(ExpandState::Retract);
        } else if (s_expandPassIndex >= s_expandPlan.passes) {
          finishExpansion();
        } else {
          setExpandState(ExpandState::PassPause);
        }
      }
      break;

    case ExpandState::Retract:
      if (updateTimedStepMove()) {
        if (s_expandPassIndex >= s_expandPlan.passes) {
          finishExpansion();
        } else {
          setExpandState(ExpandState::PassPause);
        }
      }
      break;

    case ExpandState::PassPause:
      if (millis() - s_expandStateStartMs >= s_expandPlan.passPauseMs) {
        s_expandPassIndex++;
        startTimedStepMove(static_cast<int32_t>(s_expandPlan.feedSteps),
                           s_expandPlan.feedHz);
        setExpandState(ExpandState::FeedOut);
      }
      break;

    case ExpandState::Idle:
    case ExpandState::Done:
    case ExpandState::Aborted:
      finishExpansion();
      break;
  }
}

static void sendTelemetry(bool force = false) {
  uint32_t now = millis();
  if (!force && now - s_lastTelemetryMs < MOTOR_TELEMETRY_PERIOD_MS) {
    return;
  }
  s_lastTelemetryMs = now;

  const float stepSenseAV = readStepSenseVoltage(STEP_SENSE_A_ADC_PIN);
  const float stepSenseBV = readStepSenseVoltage(STEP_SENSE_B_ADC_PIN);
  const float stepSenseAA = stepSenseVoltageToCurrent(stepSenseAV);
  const float stepSenseBA = stepSenseVoltageToCurrent(stepSenseBV);
  const float stepCurrentAbsA =
      (fabsf(stepSenseAA) > fabsf(stepSenseBA)) ? fabsf(stepSenseAA)
                                                : fabsf(stepSenseBA);
  const float adc27V = readAdcVoltage(27);
  const float adc32V = readAdcVoltage(32);
  const float adc33V = readAdcVoltage(33);
  const float adc34V = readAdcVoltage(34);
  const float adc35V = readAdcVoltage(35);
  const float adc36V = readAdcVoltage(36);
  const float adc39V = readAdcVoltage(39);
  const float acce1XV = readAdcVoltage(ACCE1_X_ADC_PIN);
  const float acce1YV = readAdcVoltage(ACCE1_Y_ADC_PIN);
  const float acce1ZV = readAdcVoltage(ACCE1_Z_ADC_PIN);
  const float acce1XG = accelVoltageToG(acce1XV);
  const float acce1YG = accelVoltageToG(acce1YV);
  const float acce1ZG = accelVoltageToG(acce1ZV);

  char line[1180];
  snprintf(
      line,
      sizeof(line),
      "TEL,ms=%lu,motor_id=1,duty=%d,enabled=%d,rpm=%.3f,current_a=%.4f,"
      "current_v=%.4f,speed_v=%.4f,encoder_rpm=%.3f,step_hz=%d,estop=%d,"
      "state=%s,mode=%s,expand_state=%s,expand_pass=%u,expand_passes=%u,"
      "expand_est_step=%ld,valve_mask=%u,valve_move_push=%d,"
      "valve_move_pull=%d,valve_drill_push=%d,valve_drill_pull=%d,"
      "valve_grinder_air=%d,adc32_v=%.4f,adc33_v=%.4f,adc34_v=%.4f,adc35_v=%.4f,"
      "adc36_v=%.4f,adc39_v=%.4f,adc27_v=%.4f,"
      "acce1_x_v=%.4f,acce1_y_v=%.4f,acce1_z_v=%.4f,"
      "acce1_x_g=%.4f,acce1_y_g=%.4f,acce1_z_g=%.4f,"
      "step_sense_a_v=%.4f,step_sense_b_v=%.4f,"
      "step_sense_a_a=%.4f,step_sense_b_a=%.4f,step_current_abs_a=%.4f\n",
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
      g_estop ? "estop" : (s_runMode == RunMode::Expand
                                ? expandStateName()
                                : (motor_is_enabled() ? "move" : "stop")),
      runModeName(),
      expandStateName(),
      static_cast<unsigned>(s_expandPassIndex),
      static_cast<unsigned>(s_expandPlan.passes),
      static_cast<long>(s_expandEstimatedStep),
      static_cast<unsigned>(valveMask()),
      s_valves[VALVE_MOVE_PUSH].on ? 1 : 0,
      s_valves[VALVE_MOVE_PULL].on ? 1 : 0,
      s_valves[VALVE_DRILL_PUSH].on ? 1 : 0,
      s_valves[VALVE_DRILL_PULL].on ? 1 : 0,
      s_valves[VALVE_GRINDER_AIR].on ? 1 : 0,
      adc32V,
      adc33V,
      adc34V,
      adc35V,
      adc36V,
      adc39V,
      adc27V,
      acce1XV,
      acce1YV,
      acce1ZV,
      acce1XG,
      acce1YG,
      acce1ZG,
      stepSenseAV,
      stepSenseBV,
      stepSenseAA,
      stepSenseBA,
      stepCurrentAbsA);
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
    stopStepClock();
    turnAllValvesOff();
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

  if (line.startsWith("VALVE,")) {
    handleValveCommand(line);
    sendTelemetry(true);
    return;
  }

  if (line.startsWith("EXPAND,")) {
    if (line.startsWith("EXPAND,START")) {
      ExpandPlan plan;
      plan.dcDuty = readIntParam(line, "dc", EXPAND_DEFAULT_DC_DUTY);
      plan.feedSteps = static_cast<uint32_t>(
          readIntParam(line, "feed_steps", EXPAND_DEFAULT_FEED_STEPS));
      plan.retractSteps = static_cast<uint32_t>(
          readIntParam(line, "retract_steps", EXPAND_DEFAULT_RETRACT_STEPS));
      plan.feedHz =
          static_cast<uint32_t>(readIntParam(line, "feed_hz", EXPAND_DEFAULT_FEED_HZ));
      plan.spinupMs = static_cast<uint32_t>(
          readIntParam(line, "spinup_ms", EXPAND_DEFAULT_SPINUP_MS));
      plan.dwellMs =
          static_cast<uint32_t>(readIntParam(line, "dwell_ms", EXPAND_DEFAULT_DWELL_MS));
      plan.passPauseMs = static_cast<uint32_t>(
          readIntParam(line, "pass_pause_ms", EXPAND_DEFAULT_PASS_PAUSE_MS));
      plan.passes =
          static_cast<uint8_t>(readIntParam(line, "passes", EXPAND_DEFAULT_PASSES));
      startExpansion(plan);
    } else if (line.startsWith("EXPAND,STOP")) {
      abortExpansion("command");
    } else if (line.startsWith("EXPAND,HOME")) {
      stepper::setHome();
      s_expandEstimatedStep = 0;
      Serial2.println("EXPAND,home,estimated_step=0");
    } else if (line.startsWith("EXPAND,STATUS")) {
      Serial2.printf("EXPAND,status,mode=%s,state=%s,pass=%u,passes=%u,"
                     "estimated_step=%ld\n",
                     runModeName(),
                     expandStateName(),
                     static_cast<unsigned>(s_expandPassIndex),
                     static_cast<unsigned>(s_expandPlan.passes),
                     static_cast<long>(s_expandEstimatedStep));
    }
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
      if (g_estop && s_runMode == RunMode::Expand) {
        abortExpansion("trigger");
      }
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
  initValves();
  pinMode(STEP_SENSE_A_ADC_PIN, INPUT);
  pinMode(STEP_SENSE_B_ADC_PIN, INPUT);
  pinMode(ACCE1_X_ADC_PIN, INPUT);
  pinMode(ACCE1_Y_ADC_PIN, INPUT);
  pinMode(ACCE1_Z_ADC_PIN, INPUT);
  analogSetPinAttenuation(STEP_SENSE_A_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(STEP_SENSE_B_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(ACCE1_X_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(ACCE1_Y_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(ACCE1_Z_ADC_PIN, ADC_11db);

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
    stopStepClock();
#endif
    s_stepDirInited = false;
  } else if (s_runMode == RunMode::Expand) {
    updateExpansion();
  } else {
    motor(g_dcDuty);
#if !USB_TELEMETRY
    updateStepper(g_stepVel);
#endif
  }

  updateValves();
  motor_update_telemetry();
  sendTelemetry();
}
