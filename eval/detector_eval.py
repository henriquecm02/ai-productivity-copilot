"""Mede o detector determinístico de injeção (sem LLM) nos conjuntos de desenvolvimento e teste.

Uso: python eval/detector_eval.py [dev|teste|todos]   (padrão: todos)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from copilot.planner import looks_like_injection  # noqa: E402

DADOS = json.loads((ROOT / "eval" / "injecoes.json").read_text(encoding="utf-8"))


def medir(conjunto: str) -> dict:
    casos = DADOS[conjunto]
    pos = [c for c in casos if c["injecao"]]
    neg = [c for c in casos if not c["injecao"]]
    detect = [c["texto"] for c in pos if looks_like_injection(c["texto"])]
    falsos = [c["texto"] for c in neg if looks_like_injection(c["texto"])]
    return {"conjunto": conjunto, "injecoes_detectadas": f"{len(detect)}/{len(pos)}",
            "falsos_positivos": f"{len(falsos)}/{len(neg)}",
            "perdidas": [c["texto"] for c in pos if c["texto"] not in detect], "falsos": falsos}


if __name__ == "__main__":
    alvo = sys.argv[1] if len(sys.argv) > 1 else "todos"
    for c in (["dev", "teste"] if alvo == "todos" else [alvo]):
        r = medir(c)
        print(f"{c}: injeções detectadas {r['injecoes_detectadas']}, falsos positivos {r['falsos_positivos']}")
        for t in r["perdidas"]:
            print("  perdeu:", t)
        for t in r["falsos"]:
            print("  falso :", t)
