from pathlib import Path

from streamlit.testing.v1 import AppTest

from cesta_inteligente.database import initialize_database, insert_receipt


APP_PATH = Path(__file__).parents[1] / "dashboard" / "app.py"


def test_dashboard_renders_useful_empty_state(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "empty-dashboard.db"
    initialize_database(db_path)
    monkeypatch.setenv("CESTA_DB_PATH", str(db_path))

    app = AppTest.from_file(str(APP_PATH)).run(timeout=10)

    assert not app.exception
    assert any("Cesta Inteligente" in title.value for title in app.title)
    assert any("primer ticket" in subheader.value for subheader in app.subheader)


def test_dashboard_renders_with_v3_schema(tmp_path: Path, mercadona_case, carrefour_confirmed_case, monkeypatch):
    receipt, _ = mercadona_case
    second_receipt, _ = carrefour_confirmed_case
    db_path = tmp_path / "dashboard.db"
    insert_receipt(db_path, receipt)
    insert_receipt(db_path, second_receipt)
    monkeypatch.setenv("CESTA_DB_PATH", str(db_path))

    app = AppTest.from_file(str(APP_PATH)).run(timeout=10)

    assert not app.exception
    assert any("Cesta Inteligente" in title.value for title in app.title)
    metric_values = [metric.value for metric in app.metric]
    assert "161,20 €" in metric_values
    assert "2" in metric_values
    assert any("Evolución mensual" in subheader.value for subheader in app.subheader)
