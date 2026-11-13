from pathlib import Path


def test_detection_never_reads_ground_truth():
    for f in Path("detection").glob("*.py"):
        assert "PlantedLeak" not in f.read_text(), f"{f} reads the answer key"