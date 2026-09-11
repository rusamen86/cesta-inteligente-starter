from pathlib import Path


def test_private_runtime_material_is_ignored():
    root = Path(__file__).parents[1]
    rules = (root / ".gitignore").read_text(encoding="utf-8")
    for required in ("data/*.db", "receipts/**", "logs/**", ".env", "credentials/**"):
        assert required in rules

