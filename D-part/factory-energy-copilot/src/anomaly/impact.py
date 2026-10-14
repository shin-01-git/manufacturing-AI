"""Anomaly impact / noise judgment (6.5 / B6, O-2 undecided).

Reports the energy/bill impact of an anomaly segment **only when it is
measurable**. If the deviation falls inside the noise range, it records
``영향 없음(잡음 범위)`` instead of a number. Unmeasured values are never printed
as numbers.

The impact definition and the "noise range" threshold are **unapproved (O-2)**,
so this module does not treat any impact figure as a confident metric. It exposes
the inputs and the measurable/unmeasurable decision, and labels every output as
provisional pending O-2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import config

# O-2 is undecided: no approved noise threshold or impact definition exists.
O2_APPROVED = False
NO_IMPACT_LABEL = "영향 없음(잡음 범위)"
UNMEASURED_LABEL = "미측정"


@dataclass
class ImpactResult:
    measurable: bool
    is_noise: bool
    label: str
    bill_impact: float | None
    carbon_impact: float | None
    energy_delta: float | None
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "measurable": self.measurable,
            "is_noise": self.is_noise,
            "label": self.label,
            # Numbers are only ever present when measurable is True.
            "bill_impact": self.bill_impact,
            "carbon_impact": self.carbon_impact,
            "energy_delta": self.energy_delta,
            "note": self.note,
            "o2_approved": O2_APPROVED,
            "provisional": True,
        }


def assess_impact(
    energy_delta: float | None,
    *,
    measurable: bool,
    is_noise: bool = False,
) -> ImpactResult:
    """Assess the impact of an anomaly segment under the O-2 (undecided) policy.

    - ``measurable=False`` → ``미측정``; no numbers are produced.
    - ``is_noise=True``    → ``영향 없음(잡음 범위)``; no numbers are produced.
    - otherwise            → bill/carbon impact derived from ``energy_delta`` via
      the single config rates, flagged provisional (O-2 unapproved).
    """

    if not measurable or energy_delta is None:
        return ImpactResult(
            measurable=False,
            is_noise=False,
            label=UNMEASURED_LABEL,
            bill_impact=None,
            carbon_impact=None,
            energy_delta=None,
            note="측정 불가: 수치를 표시하지 않음 (B6). O-2 영향 정의 미승인.",
        )

    if is_noise:
        return ImpactResult(
            measurable=True,
            is_noise=True,
            label=NO_IMPACT_LABEL,
            bill_impact=None,
            carbon_impact=None,
            energy_delta=None,
            note="잡음 범위로 판정: 수치 미표시 (B6). 잡음 기준은 O-2 미승인.",
        )

    bill = energy_delta * config.BILL_RATE
    carbon = energy_delta * config.CARBON_RATE
    return ImpactResult(
        measurable=True,
        is_noise=False,
        label="측정값(잠정)",
        bill_impact=bill,
        carbon_impact=carbon,
        energy_delta=energy_delta,
        note="측정된 전력량 차이에서 환산 (잠정). 확정 지표 아님 (O-2 미승인).",
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Assess anomaly energy/bill impact (O-2 provisional)")
    parser.add_argument(
        "--energy-delta",
        type=float,
        default=None,
        help="Measured energy delta (kWh). Omit to demonstrate the unmeasured path.",
    )
    parser.add_argument("--measurable", action="store_true", help="Mark the segment as measurable.")
    parser.add_argument("--noise", action="store_true", help="Mark the segment as noise-range.")
    args = parser.parse_args(argv)

    result = assess_impact(
        args.energy_delta,
        measurable=args.measurable,
        is_noise=args.noise,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
