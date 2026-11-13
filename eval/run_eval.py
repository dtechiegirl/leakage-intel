import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Alert, PlantedLeak


def main():
    with SessionLocal() as s:
        truth = defaultdict(set)
        for leak in s.scalars(select(PlantedLeak)):
            truth[leak.leak_type].add(leak.record_ids["invoice_ids"][0])
        predicted = defaultdict(set)
        for a in s.scalars(select(Alert)):
            predicted[a.alert_type].add(a.invoice_id)

    report = {}
    print(f"{'leak type':<20}{'planted':>8}{'flagged':>8}{'caught':>8}{'precision':>10}{'recall':>8}")
    for kind in sorted(set(truth) | set(predicted)):
        t, p = truth[kind], predicted[kind]
        caught = len(t & p)
        precision = caught / len(p) if p else 0.0
        recall = caught / len(t) if t else 0.0
        report[kind] = {
            "planted": len(t), "flagged": len(p), "caught": caught,
            "precision": round(precision, 3), "recall": round(recall, 3),
            "missed_invoice_ids": sorted(t - p),
            "false_alarm_invoice_ids": sorted(p - t),
        }
        print(f"{kind:<20}{len(t):>8}{len(p):>8}{caught:>8}{precision:>10.2f}{recall:>8.2f}")
        if t - p:
            print("   missed:", sorted(t - p))
        if p - t:
            print("   false alarms:", sorted(p - t))

    Path("eval/results").mkdir(parents=True, exist_ok=True)
    Path("eval/results/latest.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()