# ESP32 + ESCON Module telemetry setup

This folder is the experimental copy only. Do not replace the existing
`/home/haikan/haikan` environment with these files until the wiring and values
are checked.

## Purpose

The firmware keeps the existing Raspberry Pi -> ESP32 joystick command format:

```text
AXIS,ABS_Y,<0-1023>
AXIS,ABS_X,<0-1023>
BTN,BTN_TRIGGER,1
```

It adds telemetry from ESP32 -> Raspberry Pi:

```text
TEL,ms=1234,motor_id=1,duty=120,enabled=1,rpm=35.2,current_a=0.42,current_v=0.4200,speed_v=1.6500,encoder_rpm=34.8,step_hz=0,estop=0,state=move
```

The Raspberry Pi logger at
`/home/haikan/pipe_robot_dev/test/telemetry_logger.py` can save this to CSV.

## ESP32 wiring assumptions

Existing control pins:

```text
ESCON PWM    GPIO21
ESCON DIR    GPIO23
ESCON ENABLE GPIO22
ESCON STOP   GPIO19
```

Encoder feedback:

```text
Encoder A GPIO34
Encoder B GPIO35
Encoder Z GPIO32
```

Current monitor:

```text
ESCON Analog Monitor 1 -> ESP32 GPIO36
GND shared between ESCON/ESP32
```

Speed monitor:

```text
ESCON Analog Monitor 2 -> ESP32 GPIO39
GND shared between ESCON/ESP32
```

Important: ESP32 ADC input must not exceed 3.3 V.

## ESCON Studio settings to check

Use ESCON Studio and keep the current motor-control settings as much as
possible. Only add/check monitor outputs.

Recommended monitor setting:

```text
Analog Monitor 1: Actual current value
Output range: 0..3.3 V if available, otherwise use a divider/protection
```

Current speed-monitor setting under test:

```text
Analog Monitor 2: Actual speed value
ESP32 input: GPIO39
```

Important current issue:

ESCON Studio showed that the actual-speed monitor can become a negative voltage.
ESP32 ADC cannot read negative voltage, so the firmware `rpm` value is valid
only if ESCON AO2 is configured to output a unipolar 0..3.3 V signal.

If AO2 is mapped as `-max rpm -> 0 V`, `0 rpm -> 1.65 V`,
`+max rpm -> 3.3 V`, set the speed constants in `config.h` accordingly.
Until that is fixed, use `speed_v` and ESCON Studio together for debugging, and
do not treat `rpm=0` as proof that the motor is stopped.

## Calibration constants

Edit these in `config.h` after checking the actual encoder and monitor scale:

```cpp
constexpr float ENCODER_COUNTS_PER_REV = 1024.0f;
constexpr float ESCON_CURRENT_A_PER_V = 1.0f;
constexpr float ESCON_CURRENT_A_OFFSET = 0.0f;
constexpr float ESCON_SPEED_RPM_PER_V = 9690.0f / 3.3f;
constexpr float ESCON_SPEED_RPM_OFFSET = 0.0f;
```

For example, if ESCON Studio shows 2.5 A while ESP32 reads 1.25 V:

```text
ESCON_CURRENT_A_PER_V = 2.0
ESCON_CURRENT_A_OFFSET = 0.0
```

For a signed speed monitor remapped to 0..3.3 V:

```text
ESCON_SPEED_RPM_PER_V = 9690.0 / 1.65
ESCON_SPEED_RPM_OFFSET = -9690.0
```

This corresponds to:

```text
0.00 V -> -9690 rpm
1.65 V -> 0 rpm
3.30 V -> +9690 rpm
```

## Raspberry Pi logging

Run:

```bash
cd /home/haikan/pipe_robot_dev/test
python3 telemetry_logger.py --no-esp1
```

or log both ESP1 and ESP2:

```bash
python3 telemetry_logger.py
```

CSV files are saved in:

```text
/home/haikan/pipe_robot_logs
```
