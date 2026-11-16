"""Armazenamento local: tarefas (SQLite), notas (Markdown) e agenda (arquivo .ics, somente leitura).

Tudo fica numa pasta de trabalho do usuário. Nada sai da máquina, exceto o texto enviado ao LLM
quando a extração de tarefas é usada.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

PRIORITIES = ("alta", "media", "baixa")


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.notes = self.root / "notas"
        self.notes.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "tarefas.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS tarefas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT NOT NULL, prazo TEXT, prioridade TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'aberta', origem TEXT, criada_em TEXT NOT NULL, concluida_em TEXT)""")
        self.db.commit()

    # ------------------------------------------------------------------ tarefas
    def add_task(self, titulo: str, prazo: str | None = None, prioridade: str = "media", origem: str | None = None) -> dict:
        titulo = titulo.strip()
        if not titulo:
            raise ValueError("Título vazio.")
        if prioridade not in PRIORITIES:
            raise ValueError(f"Prioridade deve ser uma de {PRIORITIES}.")
        if prazo:
            date.fromisoformat(prazo)  # valida AAAA-MM-DD
        dup = self.db.execute("SELECT id FROM tarefas WHERE lower(titulo)=lower(?) AND status='aberta'",
                              (titulo,)).fetchone()
        if dup:
            return {"id": dup["id"], "duplicada": True}
        cur = self.db.execute("INSERT INTO tarefas (titulo, prazo, prioridade, origem, criada_em) VALUES (?,?,?,?,?)",
                              (titulo, prazo, prioridade, origem, datetime.now().isoformat(timespec="seconds")))
        self.db.commit()
        return {"id": cur.lastrowid, "duplicada": False}

    def list_tasks(self, status: str = "aberta", ate: str | None = None) -> list[dict]:
        q = "SELECT * FROM tarefas WHERE status = ?"
        params: list = [status]
        if ate:
            q += " AND prazo IS NOT NULL AND prazo <= ?"
            params.append(ate)
        q += (" ORDER BY CASE WHEN prazo IS NULL THEN 1 ELSE 0 END, prazo, "
              "CASE prioridade WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END")
        return [dict(r) for r in self.db.execute(q, params)]

    def complete_task(self, task_id: int) -> dict:
        cur = self.db.execute("UPDATE tarefas SET status='concluida', concluida_em=? WHERE id=? AND status='aberta'",
                              (datetime.now().isoformat(timespec="seconds"), task_id))
        self.db.commit()
        return {"id": task_id, "atualizada": cur.rowcount == 1}

    # ------------------------------------------------------------------ notas
    def save_note(self, titulo: str, conteudo: str) -> dict:
        slug = re.sub(r"[^a-z0-9]+", "-", titulo.lower()).strip("-")[:60] or "nota"
        path = self.notes / f"{date.today().isoformat()}_{slug}.md"
        path.write_text(f"# {titulo}\n\n{conteudo.strip()}\n", encoding="utf-8")
        return {"arquivo": path.name}

    def search_notes(self, termo: str, limite: int = 5) -> list[dict]:
        termo_l = termo.lower()
        hits = []
        for p in sorted(self.notes.glob("*.md"), reverse=True):
            text = p.read_text(encoding="utf-8")
            if termo_l in text.lower():
                i = text.lower().index(termo_l)
                hits.append({"arquivo": p.name, "trecho": text[max(0, i - 80): i + 160].replace("\n", " ")})
            if len(hits) >= limite:
                break
        return hits


# ---------------------------------------------------------------------- agenda (.ics)

@dataclass
class Event:
    titulo: str
    inicio: datetime
    fim: datetime
    local: str | None = None

    def as_dict(self) -> dict:
        return {"titulo": self.titulo, "inicio": self.inicio.isoformat(timespec="minutes"),
                "fim": self.fim.isoformat(timespec="minutes"), "local": self.local}


def _ics_dt(v: str) -> datetime:
    v = v.rstrip("Z")
    return datetime.strptime(v, "%Y%m%dT%H%M%S") if "T" in v else datetime.strptime(v, "%Y%m%d")


def read_ics(path: Path) -> list[Event]:
    """Leitor mínimo de iCalendar (VEVENT com DTSTART/DTEND/SUMMARY/LOCATION; sem recorrência)."""
    text = re.sub(r"\r?\n[ \t]", "", Path(path).read_text(encoding="utf-8"))  # desfaz linhas dobradas
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        f = {}
        for line in block.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                f[k.split(";")[0].upper()] = v.strip()
        if "DTSTART" in f:
            start = _ics_dt(f["DTSTART"])
            end = _ics_dt(f["DTEND"]) if "DTEND" in f else start + timedelta(hours=1)
            events.append(Event(f.get("SUMMARY", "(sem título)"), start, end, f.get("LOCATION")))
    return sorted(events, key=lambda e: e.inicio)


def events_on(events: list[Event], day: date) -> list[Event]:
    return [e for e in events if e.inicio.date() <= day <= (e.fim - timedelta(seconds=1)).date()]


def free_slots(events: list[Event], day: date, start_h: int = 9, end_h: int = 18, min_minutes: int = 30) -> list[dict]:
    cur = datetime.combine(day, datetime.min.time()).replace(hour=start_h)
    end = cur.replace(hour=end_h)
    slots = []
    for e in sorted(events_on(events, day), key=lambda e: e.inicio):
        if e.inicio > cur and (e.inicio - cur).total_seconds() / 60 >= min_minutes:
            slots.append({"inicio": cur.strftime("%H:%M"), "fim": min(e.inicio, end).strftime("%H:%M")})
        cur = max(cur, e.fim)
    if end > cur and (end - cur).total_seconds() / 60 >= min_minutes:
        slots.append({"inicio": cur.strftime("%H:%M"), "fim": end.strftime("%H:%M")})
    return slots
