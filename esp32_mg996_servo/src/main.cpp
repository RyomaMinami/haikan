/*
  ESP32 + KONDO KRS-9004HV ICS through BSS138 level shifter

  This is for ICS serial control, not RC PWM control.

  Wiring with Akizuki AE-LCNV4-MOSFET(BSS138):
    ESP32 3V3      -> Level shifter LV
    ESP32 5V       -> Level shifter HV
    ESP32 GND      -> Level shifter GND and servo power GND
    ESP32 GPIO26   -> Level shifter LV2
    Level shifter HV2 -> Servo signal
    Servo power +  -> 9-12 V HV servo power supply
    Servo power -  -> ESP32/level shifter GND

  KRS-9004HV ICS is an HV servo. Do not drive it from 5 V.

  Notes:
    - This sketch is TX-only, so it does not read the servo reply.
    - Connect the servo signal to HV2, not LV2.
    - The BSS138 board is a level shifter, not a KONDO ICS USB adapter.
*/

#include <Arduino.h>

const uint8_t SERVO_ID = 5;

const int ICS_TX_PIN = 26;

const uint32_t ICS_BAUD = 115200;
const uint32_t COMMAND_PERIOD_MS = 20;

HardwareSerial IcsSerial(2);
uint16_t targetPosition = 7500;
uint32_t lastCommandMs = 0;
bool verboseTx = true;
bool lineTestMode = false;
bool autoMoveMode = false;
uint32_t lastAutoMoveMs = 0;
uint8_t autoMoveStep = 0;

uint8_t posHigh(uint16_t pos) {
  return (pos >> 7) & 0x7F;
}

uint8_t posLow(uint16_t pos) {
  return pos & 0x7F;
}

void sendIcsPosition(uint16_t position, bool verbose) {
  position = constrain(position, 0, 11500);

  const uint8_t cmd[3] = {
      (uint8_t)(0x80 | (SERVO_ID & 0x1F)),
      posHigh(position),
      posLow(position),
  };

  IcsSerial.write(cmd, sizeof(cmd));
  IcsSerial.flush();

  if (verbose) {
    Serial.print("TX:");
    for (uint8_t b : cmd) {
      Serial.printf(" %02X", b);
    }
    Serial.println();
  }
}

void printHelp() {
  Serial.println();
  Serial.println("KONDO KRS-9004HV ICS BSS138 TX-only test");
  Serial.println("Servo ID: 5");
  Serial.println("Commands:");
  Serial.println("  c : center, position value 7500");
  Serial.println("  l : left, position value 6000");
  Serial.println("  r : right, position value 9000");
  Serial.println("  0 : min limit, position value 3500");
  Serial.println("  5 : center, position value 7500");
  Serial.println("  9 : max limit, position value 11500");
  Serial.println("  f : free, position value 0");
  Serial.println("  a : toggle auto move test, 6000 -> 9000 -> 7500");
  Serial.println("  t : toggle GPIO26/HV2 line test");
  Serial.println("  h : help");
  Serial.println();
}

void commandPosition(uint16_t position, const char *label) {
  targetPosition = constrain(position, 0, 11500);
  sendIcsPosition(targetPosition, true);

  Serial.print(label);
  Serial.print(" target=");
  Serial.print(targetPosition);
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  delay(300);

  pinMode(ICS_TX_PIN, OUTPUT);
  digitalWrite(ICS_TX_PIN, HIGH);
  delay(700);

  // ICS serial settings: 8 data bits, even parity, 1 stop bit.
  IcsSerial.begin(ICS_BAUD, SERIAL_8E1, -1, ICS_TX_PIN);
  delay(100);

  printHelp();
  commandPosition(7500, "startup center");
}

void loop() {
  if (lineTestMode) {
    digitalWrite(ICS_TX_PIN, HIGH);
    Serial.println("line test: HIGH");
    delay(1000);
    digitalWrite(ICS_TX_PIN, LOW);
    Serial.println("line test: LOW");
    delay(1000);

    if (Serial.available() > 0) {
      const char c = Serial.read();
      if (c == 't' || c == 'T') {
        lineTestMode = false;
        IcsSerial.begin(ICS_BAUD, SERIAL_8E1, -1, ICS_TX_PIN);
        Serial.println("line test: off");
      }
    }
    return;
  }

  const uint32_t now = millis();

  if (autoMoveMode && now - lastAutoMoveMs >= 1500) {
    lastAutoMoveMs = now;
    if (autoMoveStep == 0) {
      commandPosition(6000, "auto left");
    } else if (autoMoveStep == 1) {
      commandPosition(9000, "auto right");
    } else {
      commandPosition(7500, "auto center");
    }
    autoMoveStep = (autoMoveStep + 1) % 3;
  }

  if (now - lastCommandMs >= COMMAND_PERIOD_MS) {
    lastCommandMs = now;
    sendIcsPosition(targetPosition, false);
  }

  if (Serial.available() <= 0) {
    delay(10);
    return;
  }

  const char c = Serial.read();
  switch (c) {
    case 'c':
    case 'C':
    case '5':
      commandPosition(7500, "center");
      break;
    case 'l':
    case 'L':
      commandPosition(6000, "left");
      break;
    case 'r':
    case 'R':
      commandPosition(9000, "right");
      break;
    case '0':
      commandPosition(3500, "min");
      break;
    case '9':
      commandPosition(11500, "max");
      break;
    case 'f':
    case 'F':
      commandPosition(0, "free");
      break;
    case 'a':
    case 'A':
      autoMoveMode = !autoMoveMode;
      lastAutoMoveMs = 0;
      autoMoveStep = 0;
      Serial.print("auto move: ");
      Serial.println(autoMoveMode ? "on" : "off");
      break;
    case 't':
    case 'T':
      IcsSerial.end();
      pinMode(ICS_TX_PIN, OUTPUT);
      lineTestMode = true;
      Serial.println("line test: on");
      break;
    case 'h':
    case 'H':
      printHelp();
      break;
    default:
      break;
  }
}
