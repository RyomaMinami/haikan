# ESCON/ESP32 telemetry work notes for next operator

This folder is a backup and handover package for the ESP32 + ESCON telemetry work done on 2026-07-05.

## What is in this folder

- `original_from_downloads/`
  - Original program files received from the user before this work.
  - Source: `C:\Users\minam\Downloads`
  - Files: `main.cpp`, `motor.cpp`, `step.cpp`, `config.h`, `motor.h`, `step.h`
- `current_modified/esp32_escon_telemetry/`
  - Modified PlatformIO project currently used for ESP32.
- `current_modified/raspi_scripts/`
  - Raspberry Pi logging and drive-test scripts.
- `logs/`
  - Representative logs taken during this debugging session.
- `ESCON_TELEMETRY_CHANGELOG_20260705.md`
  - Detailed change log, observations, and next actions.

## Current hardware assumptions

- Raspberry Pi SSH:
  - `ssh -i C:\Users\minam\yes haikan@192.168.50.154`
- Raspberry Pi test directory:
  - `/home/haikan/pipe_robot_dev/test`
- Raspberry Pi log directory:
  - `/home/haikan/pipe_robot_logs`
- ESP32 firmware project on PC:
  - `C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools\esp32_escon_telemetry`
- PlatformIO executable on PC:
  - `C:\Users\minam\AppData\Local\Programs\Python\Python313\Scripts\pio.exe`

## ESP32 pin mapping used in the modified firmware

- ESCON current analog monitor: `AD1`, ESP32 `GPIO36`
- ESCON speed analog monitor: `AD2`, ESP32 `GPIO39`
- ESCON enable output from ESP32: `GPIO22`
- ESCON direction output from ESP32: `GPIO23`
- ESCON PWM command output from ESP32: `GPIO21`
- ESCON stop output from ESP32: `GPIO19`

## Important current conclusion

The ESP32 can read positive analog values, but the ESCON actual-speed analog output appears to be signed/bipolar.

During ESCON Studio monitoring, actual motor speed was around negative rpm and analog output 2 looked like a negative voltage. ESP32 ADC pins cannot measure negative voltage, so the ESP32-side `rpm` field can become 0 even while the motor is actually rotating.

Do not assume `rpm=0` means the motor is stopped until the ESCON AO2 scaling is corrected or a level-shift circuit is added.

## Useful commands

Build ESP32 firmware:

```powershell
cd C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools\esp32_escon_telemetry
C:\Users\minam\AppData\Local\Programs\Python\Python313\Scripts\pio.exe run
```

Upload ESP32 firmware by OTA:

```powershell
cd C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools\esp32_escon_telemetry
C:\Users\minam\AppData\Local\Programs\Python\Python313\Scripts\pio.exe run -e esp32dev_ota_wifi -t upload
```

SSH to Raspberry Pi:

```powershell
ssh -i C:\Users\minam\yes haikan@192.168.50.154
```

Short telemetry log on Raspberry Pi:

```bash
cd /home/haikan/pipe_robot_dev/test
timeout 4s python3 telemetry_logger.py --no-esp1 --esp2-port /dev/ttyAMA4 --output-dir /home/haikan/pipe_robot_logs
```

Drive test with logging:

```bash
cd /home/haikan/pipe_robot_dev/test
python3 escon_drive_log_test.py --forward-y 760 --reverse-y 80 --run-s 6 --stop-s 2 --no-wait
```

## Safe next steps

1. Check ESCON Studio analog output 2 scaling.
2. If possible, map actual speed to a unipolar 0 to 3.3 V range with 0 rpm at about 1.65 V.
3. After changing ESCON AO2, update firmware constants:
   - `ESCON_SPEED_RPM_PER_V`
   - `ESCON_SPEED_RPM_OFFSET`
4. Re-test `current_v`, `speed_v`, `rpm`, and `encoder_rpm`.
5. Check DI2 rotation direction behavior in ESCON Studio, because the reverse command did not show current or speed in the latest logs.

