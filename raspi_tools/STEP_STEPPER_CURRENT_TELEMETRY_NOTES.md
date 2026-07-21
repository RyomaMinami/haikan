# Stepper current telemetry notes

Date: 2026-07-08

## Purpose

Add current-sense logging for the SLA7078MPRT stepper driver without changing
the existing ESCON current logging.

## Wiring under test

```text
SLA7078MPRT SenseA -> 1 kOhm -> ESP32 GPIO33
                         |
                       0.33 uF
                         |
                        GND

SLA7078MPRT SenseB -> 1 kOhm -> ESP32 GPIO27
                         |
                       0.33 uF
                         |
                        GND
```

## Firmware changes

Project:

```text
raspi_tools/esp32_escon_telemetry
```

Added telemetry fields:

```text
adc27_v
step_sense_a_v
step_sense_b_v
step_sense_a_a
step_sense_b_a
step_current_abs_a
```

GPIO mapping:

```text
STEP_SENSE_A_ADC_PIN = 33
STEP_SENSE_B_ADC_PIN = 27
```

The current conversion is provisional:

```text
current_A = voltage_V / STEP_SENSE_RESISTOR_OHM
```

`STEP_SENSE_RESISTOR_OHM` is currently `0.155`, based on the SLA7078MPRT
internal sense resistor typical value. This gives:

```text
phase_current_A = sense_voltage_V / 0.155 ohm
```

This is still an estimate. The Sense pin is a chopper/PWM waveform, and the
external RC filter gives an averaged voltage rather than a synchronized peak.
Keep `step_sense_a_v` and `step_sense_b_v` in the CSV so the conversion can be
revisited later.

## Important caveat

GPIO27 is ADC2. ESP32 ADC2 can be unavailable or noisy while Wi-Fi is active.
Because this firmware keeps Wi-Fi active for OTA, `step_sense_b_v` may be less
reliable than `step_sense_a_v`. If B phase does not respond, either:

1. Move SenseB to an ADC1 pin, or
2. Disable Wi-Fi after boot/OTA window, then read ADC2.

## Raspberry Pi scripts

Updated CSV columns in:

```text
/home/haikan/pipe_robot_dev/test/telemetry_logger.py
/home/haikan/pipe_robot_dev/test/escon_drive_log_test.py
/home/haikan/pipe_robot_dev/test/escon_speed_ramp_log_test.py
```

Added:

```text
/home/haikan/pipe_robot_dev/test/stepper_current_log_test.py
```

Suggested test:

```bash
python3 /home/haikan/pipe_robot_dev/test/stepper_current_log_test.py \
  --port /dev/ttyAMA4 --abs-x 850 --warmup-s 3 --run-s 20 --cooldown-s 3
```

This logs to:

```text
/home/haikan/pipe_robot_logs/stepper_current_log_YYYYMMDD_HHMMSS.csv
```

## First baseline after OTA

Stopping state, no stepper motion:

```text
step_sense_a_v ~= 0.142 V
step_sense_b_v ~= 0.128 V
```

Next check: run the stepper and manually apply load. A valid current signal
should change from this baseline.
