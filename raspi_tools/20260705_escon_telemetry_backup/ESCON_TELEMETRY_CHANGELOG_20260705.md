# ESCON telemetry change log 2026-07-05

## Goal

Prepare the ESP32 and Raspberry Pi programs so that motor-related values can be logged for future experiments:

- ESCON current monitor value
- ESCON speed monitor value
- ESP32 encoder-derived rpm if available
- Command, direction, enable, stop, and PWM duty status

The original programs must remain preserved so another operator can compare the current state with the starting point.

## Backed-up original files

Original files copied from:

`C:\Users\minam\Downloads`

to:

`original_from_downloads/`

These files are treated as the pre-edit baseline:

- `config.h`
- `main.cpp`
- `motor.cpp`
- `motor.h`
- `step.cpp`
- `step.h`

## Modified files

Current modified copies are stored in:

- `current_modified/esp32_escon_telemetry/`
- `current_modified/raspi_scripts/`

Active working copies are:

- PC ESP32 project:
  - `C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools\esp32_escon_telemetry`
- PC Raspberry Pi scripts:
  - `C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools\telemetry_logger.py`
  - `C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools\escon_drive_log_test.py`
- Raspberry Pi scripts:
  - `/home/haikan/pipe_robot_dev/test/telemetry_logger.py`
  - `/home/haikan/pipe_robot_dev/test/escon_drive_log_test.py`

## Firmware changes

### Analog input pins

The user confirmed from the schematic:

- `AD1 = GPIO36`
- `AD2 = GPIO39`

The firmware was changed so:

- `GPIO36` reads ESCON current monitor.
- `GPIO39` reads ESCON speed monitor.

### Added telemetry fields

The ESP32 telemetry now includes:

- `current_a`
- `current_v`
- `speed_v`
- `rpm`
- `encoder_rpm`

Reason:

- `current_v` and `speed_v` are raw analog voltages and are useful for debugging scaling.
- `rpm` is the current firmware's interpreted rpm from the ESCON speed analog value.
- `encoder_rpm` preserves the direct ESP32 encoder calculation separately.

### OTA support

OTA upload has been configured and tested.

WiFi candidates:

- `aokilab2` / `aokilab0118`
- `aokilab` / `aokilab0118`

Fallback AP:

- SSID: `ESP32_ESCON_OTA`
- password: `esconota`

OTA target:

- `esp32-escon.local`

OTA password:

- `esconota`

## Raspberry Pi script changes

### `telemetry_logger.py`

Added support for the new ESP32 telemetry fields:

- `current_v`
- `speed_v`
- `encoder_rpm`

### `escon_drive_log_test.py`

Added drive-test logging so Raspberry Pi can send motor commands while recording telemetry.

This was used to compare positive and reverse command behavior.

## Observed logs

Representative logs are in `logs/`.

Important log:

- `/home/haikan/pipe_robot_logs/escon_drive_log_20260705_154809.csv`

Observed behavior:

- Forward command produced current and occasional speed analog values.
- Reverse command had `enabled=1` and negative duty command, but logged `current_v=0`, `speed_v=0`, `rpm=0`, `current_a=0`.
- `encoder_rpm` stayed 0.

Interpretation:

- The reverse command may not be reaching ESCON as intended, or the ESCON direction input logic/polarity is not as expected.
- The encoder reading path is not confirmed. It may be disconnected, wrong pins, wrong pull-up/down, or unsuitable signal format.

## ESCON Studio observations

From ESCON Studio controller monitor during a positive command:

- Enable was active.
- AI1 was about `1.49 V`.
- Actual motor current was about `0.31 A`.
- Actual speed was around `-1036 rpm`.
- Analog Output 2 appeared to be about `-3.2 V`.

Important conclusion:

The ESCON actual speed output is likely signed/bipolar. ESP32 ADC cannot read negative voltage. Therefore, the ESP32 `rpm` value can be wrong unless AO2 is remapped or electrically shifted.

## Known issue to fix next

### AO2 speed monitor scaling

Current firmware constants are:

```cpp
ESCON_SPEED_RPM_PER_V = 9690.0f / 3.3f
ESCON_SPEED_RPM_OFFSET = 0.0f
```

This only works if ESCON AO2 is already 0 to 3.3 V and unipolar. Current observation suggests it is not.

Preferred ESCON AO2 mapping:

- `-9690 rpm -> 0.0 V`
- `0 rpm -> 1.65 V`
- `+9690 rpm -> 3.3 V`

Then firmware should use:

```cpp
rpm = (speed_v - 1.65f) * (9690.0f / 1.65f);
```

If ESCON Studio cannot map bipolar speed into 0 to 3.3 V, add an external analog level-shift/bias circuit or use a digital/serial value source instead.

### Direction command

The Raspberry Pi command with reverse direction did not produce measured current or speed.

Next checks:

- Confirm ESP32 `GPIO23` physically reaches ESCON `DI2`.
- Use ESCON Studio monitor to watch DI2 while Raspberry Pi sends reverse command.
- Confirm ESCON setting for DI2 is really `Rotation direction`.
- Confirm active-high/active-low polarity.

### Encoder rpm

`encoder_rpm` stayed 0.

Next checks:

- Confirm encoder A/B wiring to ESP32.
- Confirm GPIO pins and signal voltage.
- Check pull-ups or pull-downs.
- Verify whether the ESCON/module output is quadrature, single-ended, open collector, or differential.

## Recommendation for the next operator

Before changing motor-control behavior, first fix measurement reliability:

1. Make `current_v` match ESCON Studio current monitor.
2. Make `speed_v` readable by ESP32 over the whole expected speed range.
3. Confirm DI2 direction status in ESCON Studio.
4. Only then tune motor commands and force/current experiments.

