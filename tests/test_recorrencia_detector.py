"""Eventos recorrentes (RRULE/EXDATE), expediente configurável e o detector de injeção medido em dev e teste."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from copilot.store import events_on, free_slots, read_ics, working_hours

ROOT = Path(__file__).resolve().parents[1]
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Ritual de planejamento
DTSTART:20261102T140000
DTEND:20261102T150000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261231T235959
EXDATE:20261125T140000
END:VEVENT
BEGIN:VEVENT
SUMMARY:Daily
DTSTART:20261116T093000
DTEND:20261116T094500
RRULE:FREQ=DAILY;COUNT=5
END:VEVENT
BEGIN:VEVENT
SUMMARY:Quinzenal
DTSTART:20261103T100000
DTEND:20261103T110000
RRULE:FREQ=WEEKLY;INTERVAL=2
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def evs(tmp_path):
    p = tmp_path / "rec.ics"
    p.write_text(ICS, encoding="utf-8")
    return read_ics(p)


def titulos(evs, d):
    return [e.titulo for e in events_on(evs, d)]


def test_weekly_byday_until_and_exdate(evs):
    assert "Ritual de planejamento" in titulos(evs, date(2026, 11, 23))  # segunda
    assert "Ritual de planejamento" not in titulos(evs, date(2026, 11, 24))  # terça
    assert "Ritual de planejamento" not in titulos(evs, date(2026, 11, 25))  # quarta cancelada (EXDATE)
    assert "Ritual de planejamento" in titulos(evs, date(2026, 11, 30))
    assert "Ritual de planejamento" not in titulos(evs, date(2027, 1, 4))  # depois do UNTIL
    occ = events_on(evs, date(2026, 11, 23))[0]
    assert (occ.inicio.hour, occ.fim.hour) == (14, 15)


def test_daily_count_and_interval(evs):
    assert "Daily" in titulos(evs, date(2026, 11, 20))  # 5ª ocorrência
    assert "Daily" not in titulos(evs, date(2026, 11, 21))  # COUNT=5 esgotado
    assert "Quinzenal" in titulos(evs, date(2026, 11, 17))
    assert "Quinzenal" not in titulos(evs, date(2026, 11, 10))


def test_free_slots_respect_recurrence_and_custom_hours(evs):
    d = date(2026, 11, 18)  # quarta: Daily 9:30-9:45 e Ritual 14-15
    slots = free_slots(events_on(evs, d), d, 8, 17)
    assert slots == [{"inicio": "08:00", "fim": "09:30"}, {"inicio": "09:45", "fim": "14:00"},
                     {"inicio": "15:00", "fim": "17:00"}]


def test_working_hours_env(monkeypatch):
    monkeypatch.delenv("COPILOT_EXPEDIENTE", raising=False)
    assert working_hours() == (9, 18)
    monkeypatch.setenv("COPILOT_EXPEDIENTE", "8-17")
    assert working_hours() == (8, 17)
    for ruim in ("17-8", "abc", "8-25"):
        monkeypatch.setenv("COPILOT_EXPEDIENTE", ruim)
        with pytest.raises(ValueError):
            working_hours()


def test_detector_thresholds():
    """Limites travam o resultado medido: dev 10/10 sem falso positivo; teste (separado) 8/8 com 1/8 falso positivo."""
    sys.path.insert(0, str(ROOT / "eval"))
    from detector_eval import medir

    def frac(x):
        a, b = x.split("/")
        return int(a) / int(b)

    for conjunto, min_det, max_fp in (("dev", 1.0, 0.0), ("teste", 1.0, 0.125)):
        r = medir(conjunto)
        assert frac(r["injecoes_detectadas"]) >= min_det and frac(r["falsos_positivos"]) <= max_fp, r
