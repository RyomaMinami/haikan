#!/usr/bin/env python3
"""Plot rigid drive-wheel load sharing and ridge-crossing margin."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def main() -> None:
    here = Path(__file__).resolve().parent
    output = here / "output"
    pressure = json.loads(
        (output / "pressurized_contact_distribution_rigid.json").read_text(
            encoding="utf-8"
        )
    )
    ridge = json.loads(
        (output / "ridge_crossing_force_rigid_w30_mu0p4.json").read_text(
            encoding="utf-8"
        )
    )
    cases = np.arange(11, 11 + len(pressure["primary_cases"]))
    drive_1 = np.array([
        row["drive_normal_force_n"][0] for row in pressure["primary_cases"]
    ])
    drive_2 = np.array([
        row["drive_normal_force_n"][1] for row in pressure["primary_cases"]
    ])
    traction = np.array([row["traction_limit_n"] for row in ridge["cases"]])
    resistance = np.array([
        row["maximum_forward_ridge_force_n"] for row in ridge["cases"]
    ])

    figure, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    axes[0].plot(cases, drive_1, "o-", label="drive wheel 1")
    axes[0].plot(cases, drive_2, "o-", label="drive wheel 2")
    axes[0].plot(cases, drive_1 + drive_2, "k--", label="drive-wheel total")
    axes[0].axhline(2.0, color="#777777", ls=":", label="contact threshold")
    axes[0].set_ylabel("Normal force [N]")
    axes[0].set_title("Rigid drive-wheel contact after 0.7 MPa clamping")
    axes[0].grid(alpha=0.25)
    axes[0].legend(ncol=4)

    axes[1].plot(cases, traction, "o-", color="#167d45", label="traction limit (mu=0.4)")
    axes[1].plot(cases, resistance, "o-", color="#1665a8", label="ridge resistance")
    axes[1].fill_between(
        cases, resistance, traction, where=traction >= resistance,
        color="#68b684", alpha=0.25, label="positive traction margin",
    )
    axes[1].set(
        xlabel="Contact case",
        ylabel="Axial force [N]",
        xticks=cases,
    )
    axes[1].grid(alpha=0.25)
    axes[1].legend(ncol=3)
    figure.tight_layout()
    path = output / "rigid_drive_wheel_contact_mu0p4.png"
    figure.savefig(path, dpi=180)
    plt.close(figure)
    print(path)


if __name__ == "__main__":
    main()
