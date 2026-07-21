"""
KEYENCE LK-G3000 / LkIF.dll probe.

Expected layout:
    sensor_logger/
      lk_probe.py
      LkIF.dll
      KeyUsbDrv.dll

Run:
    python lk_probe.py

If DLL loading fails with WinError 193, Python and LkIF.dll bitness do not
match. Use 32-bit Python for a 32-bit DLL, or 64-bit Python for a 64-bit DLL.
"""

from __future__ import annotations

import ctypes
import os
import platform
from ctypes import byref
from pathlib import Path


DLL_NAME = "LkIF.dll"


class LkFloatValue(ctypes.Structure):
    _fields_ = [
        ("FloatResult", ctypes.c_int),
        ("Value", ctypes.c_float),
    ]


COMMON_FUNCTIONS = [
    "LKIF_Initialize",
    "LKIF_Finalize",
    "LKIF_OpenDevice",
    "LKIF_CloseDevice",
    "LKIF_Open",
    "LKIF_Close",
    "LKIF_GetCalcData",
]


def status_name(code: int) -> str:
    return {
        0: "VALID",
        1: "RANGEOVER_N",
        2: "WAITING",
        3: "RANGEOVER_P",
        4: "ALARM",
    }.get(code, f"status_{code}")


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    dll_path = script_dir / DLL_NAME

    print(f"Python: {platform.python_version()} ({platform.architecture()[0]})")
    print(f"DLL path: {dll_path}")

    if not dll_path.exists():
        print("LkIF.dll was not found. Copy LkIF.dll and KeyUsbDrv.dll into this folder.")
        return 1

    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(script_dir))

    try:
        dll = ctypes.WinDLL(str(dll_path))
    except OSError as exc:
        print(f"Failed to load DLL: {exc}")
        print("Check Python/DLL bitness. WinError 193 usually means 32/64-bit mismatch.")
        return 1

    print("DLL loaded.")
    print("Common function check:")
    for name in COMMON_FUNCTIONS:
        print(f"  {name}: {'found' if hasattr(dll, name) else 'not found'}")

    for name in ("LKIF_Initialize", "LKIF_OpenDevice", "LKIF_Open"):
        func = getattr(dll, name, None)
        if func is None:
            continue
        try:
            func.restype = ctypes.c_bool
            print(f"{name}() -> {bool(func())}")
        except Exception as exc:
            print(f"{name}() failed: {exc}")
        break

    get_calc_data = getattr(dll, "LKIF_GetCalcData", None)
    if get_calc_data is None:
        print("LKIF_GetCalcData was not found. Check the LkIF.dll version/manual.")
        return 1

    get_calc_data.argtypes = [ctypes.POINTER(LkFloatValue), ctypes.POINTER(LkFloatValue)]
    get_calc_data.restype = ctypes.c_bool

    for i in range(10):
        out1 = LkFloatValue()
        out2 = LkFloatValue()
        ok = bool(get_calc_data(byref(out1), byref(out2)))
        print(
            f"{i + 1}: ok={ok} "
            f"OUT1={out1.Value:.6f} ({status_name(out1.FloatResult)}) "
            f"OUT2={out2.Value:.6f} ({status_name(out2.FloatResult)})"
        )

    for name in ("LKIF_CloseDevice", "LKIF_Close", "LKIF_Finalize"):
        func = getattr(dll, name, None)
        if func is None:
            continue
        try:
            func.restype = ctypes.c_bool
            print(f"{name}() -> {bool(func())}")
        except Exception as exc:
            print(f"{name}() failed: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
