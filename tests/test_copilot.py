"""Testes offline: armazenamento, agenda, plano do dia, extração (LLM roteirizado) e o servidor MCP,
inclusive por um cliente MCP real via stdio."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import anyio
import pytest

from copilot.llm import ScriptedLLM
from copilot.mcp_server import build_server
from copilot.planner import extract_tasks, plan_day
from copilot.store import Workspace, free_slots, read_ics

ROOT = Path(__file__).resolve().parents[1]
ICS = ROOT / "samples" / "agenda.ics"
DAY = date(2026, 11, 18)


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path)


def test_tasks_dedupe_validate_and_complete(ws):
    a = ws.add_task("Enviar planilha de custos", "2026-11-20", "alta")
    assert ws.add_task("enviar planilha de custos")["duplicada"] is True
    with pytest.raises(ValueError):
        ws.add_task("x", prazo="20/11/2026")
    with pytest.raises(ValueError):
        ws.add_task("x", prioridade="urgentissima")
    assert ws.complete_task(a["id"])["atualizada"] and not ws.complete_task(a["id"])["atualizada"]
    assert ws.list_tasks() == []


def test_notes_search(ws):
    ws.save_note("Reunião fornecedor", "Combinamos desconto de 5% no próximo pedido.")
    hits = ws.search_notes("desconto")
    assert len(hits) == 1 and "5%" in hits[0]["trecho"]


def test_ics_parsing_handles_folded_lines_and_all_day(ws):
    ev = read_ics(ICS)
    titles = [e.titulo for e in ev]
    assert "Revisão do orçamento de marketing" in titles
    allday = next(e for e in ev if e.titulo == "Treinamento externo")
    assert allday.inicio.date() == date(2026, 11, 19)


def test_free_slots(ws):
    slots = free_slots(read_ics(ICS), DAY)
    assert slots == [{"inicio": "09:00", "fim": "09:30"}, {"inicio": "09:45", "fim": "11:00"},
                     {"inicio": "12:00", "fim": "12:30"}, {"inicio": "13:30", "fim": "16:00"},
                     {"inicio": "17:00", "fim": "18:00"}]
    assert free_slots(read_ics(ICS), date(2026, 11, 19)) == []  # dia inteiro ocupado


def test_plan_day_prioritizes_overdue_then_today(ws):
    ws.add_task("Pagar boleto atrasado", "2026-11-15", "media")
    ws.add_task("Fechar proposta", "2026-11-18", "alta")
    ws.add_task("Ideia para depois", None, "baixa")
    p = plan_day(ws, read_ics(ICS), DAY)
    assert [t["titulo"] for t in p["atrasadas"]] == ["Pagar boleto atrasado"]
    assert [f["tarefa"] for f in p["sugestao_foco"]] == ["Pagar boleto atrasado", "Fechar proposta"]
    assert len(p["agenda"]) == 4


def test_extraction_drops_unsupported_and_past_tasks():
    text = (ROOT / "samples" / "email.txt").read_text(encoding="utf-8")
    llm = ScriptedLLM(json_fn=lambda p, s: {"tarefas": [
        {"titulo": "Enviar planilha de custos atualizada", "prazo": "2026-11-20", "prioridade": "alta",
         "trecho": "Você precisa enviar a planilha de custos atualizada até sexta."},
        {"titulo": "Agendar conversa com fornecedor de gás", "prazo": None, "prioridade": "media",
         "trecho": "Lembra de agendar a conversa com o fornecedor de gás na semana que vem."},
        {"titulo": "Aprovar orçamento", "prazo": None, "prioridade": "alta", "trecho": "Aprove o orçamento hoje."},
        {"titulo": "Algo no passado", "prazo": "2026-01-01", "prioridade": "baixa",
         "trecho": "Você precisa enviar a planilha de custos atualizada até sexta."},
    ]})
    res = extract_tasks(llm, text, DAY)
    assert [t["titulo"] for t in res["tarefas"]] == ["Enviar planilha de custos atualizada",
                                                    "Agendar conversa com fornecedor de gás"]
    assert len(res["descartadas"]) == 2  # trecho inventado e prazo no passado


def test_mcp_tools_in_process(tmp_path):
    srv = build_server(tmp_path, ICS, llm=None, today=lambda: DAY)

    async def run():
        names = {t.name for t in await srv.list_tools()}
        assert {"criar_tarefa", "listar_tarefas", "concluir_tarefa", "planejar_dia", "extrair_tarefas"} <= names
        await srv.call_tool("criar_tarefa", {"titulo": "Fechar proposta", "prazo": "2026-11-18", "prioridade": "alta"})
        r = await srv.call_tool("planejar_dia", {})
        plan = json.loads(r.content[0].text)
        assert plan["vencem_hoje"][0]["titulo"] == "Fechar proposta"
        assert {str(x.uri) for x in await srv.list_resources()} == {"tarefas://abertas", "agenda://hoje"}
        assert {p.name for p in await srv.list_prompts()} == {"revisar_meu_dia", "texto_para_tarefas"}
    anyio.run(run)


def test_mcp_over_stdio_with_real_client(tmp_path):
    """Sobe o servidor como processo e conversa pelo protocolo MCP, como um cliente real faria."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "copilot.mcp_server", "--pasta",
                                                                  str(tmp_path), "--agenda", str(ICS)],
                                   env={"PYTHONPATH": str(ROOT / "src")})

    async def run():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            assert len(tools.tools) == 7
            await s.call_tool("criar_tarefa", {"titulo": "Revisar contrato", "prazo": "2026-11-18"})
            res = await s.call_tool("planejar_dia", {"data": "2026-11-18"})
            plan = json.loads(res.content[0].text)
            assert plan["vencem_hoje"][0]["titulo"] == "Revisar contrato"
            assert plan["horarios_livres"][0] == {"inicio": "09:00", "fim": "09:30"}
            no_key = json.loads((await s.call_tool("extrair_tarefas", {"texto": "x"})).content[0].text)
            assert "GEMINI_API_KEY" in no_key["erro"]
    anyio.run(run)
