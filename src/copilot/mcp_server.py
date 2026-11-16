"""Servidor MCP do copiloto: tarefas, notas, agenda e planejamento como ferramentas padronizadas.

Qualquer cliente MCP (Claude Desktop, Claude Code, IDEs) vira o "copiloto": o modelo do cliente
conversa com a pessoa e chama estas ferramentas. Só `extrair_tarefas` usa o Gemini.

  copilot-mcp --pasta ~/copiloto --agenda ~/agenda.ics
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .planner import extract_tasks, plan_day
from .store import Workspace, read_ics


def build_server(folder: Path, ics: Path | None = None, llm=None, today=date.today) -> MCPServer:
    ws = Workspace(folder)
    srv = MCPServer(name="productivity-copilot",
                    instructions="Ferramentas locais de produtividade: tarefas, notas e agenda. Use planejar_dia para "
                                 "montar o dia e extrair_tarefas para transformar e-mails ou atas em tarefas; "
                                 "confirme com a pessoa antes de concluir tarefas.")

    def events():
        return read_ics(ics) if ics and Path(ics).exists() else []

    def dump(x) -> str:
        return json.dumps(x, ensure_ascii=False, default=str)

    @srv.tool(description="Cria uma tarefa (prazo AAAA-MM-DD opcional; prioridade alta|media|baixa). "
                          "Não duplica tarefa aberta com o mesmo título.")
    def criar_tarefa(titulo: str, prazo: str | None = None, prioridade: str = "media") -> str:
        return dump(ws.add_task(titulo, prazo, prioridade, origem="mcp"))

    @srv.tool(description="Lista tarefas abertas, opcionalmente só as com prazo até uma data.",
              annotations=ToolAnnotations(readOnlyHint=True))
    def listar_tarefas(ate: str | None = None) -> str:
        return dump(ws.list_tasks(ate=ate))

    @srv.tool(description="Marca uma tarefa como concluída. Confirme com a pessoa antes.",
              annotations=ToolAnnotations(destructiveHint=True))
    def concluir_tarefa(id: int) -> str:
        return dump(ws.complete_task(id))

    @srv.tool(description="Salva uma nota em Markdown.")
    def salvar_nota(titulo: str, conteudo: str) -> str:
        return dump(ws.save_note(titulo, conteudo))

    @srv.tool(description="Busca notas por termo.", annotations=ToolAnnotations(readOnlyHint=True))
    def buscar_notas(termo: str) -> str:
        return dump(ws.search_notes(termo))

    @srv.tool(description="Plano do dia: tarefas atrasadas e do dia, agenda, horários livres e sugestão de foco.",
              annotations=ToolAnnotations(readOnlyHint=True))
    def planejar_dia(data: str | None = None) -> str:
        return dump(plan_day(ws, events(), date.fromisoformat(data) if data else today()))

    @srv.tool(description="Extrai tarefas de um texto (e-mail, ata, mensagem) com IA. Retorna as tarefas para a "
                          "pessoa revisar; não as cria automaticamente.", annotations=ToolAnnotations(readOnlyHint=True))
    def extrair_tarefas(texto: str) -> str:
        model = llm or _default_llm()
        if model is None:
            return dump({"erro": "Defina GEMINI_API_KEY para usar a extração de tarefas."})
        return dump(extract_tasks(model, texto, today()))

    @srv.resource("tarefas://abertas", description="Tarefas abertas (JSON).")
    def tarefas_abertas() -> str:
        return dump(ws.list_tasks())

    @srv.resource("agenda://hoje", description="Eventos de hoje (JSON).")
    def agenda_hoje() -> str:
        return dump(plan_day(ws, events(), today())["agenda"])

    @srv.prompt(description="Revisão rápida do dia: prioridades, agenda e onde encaixar o trabalho focado.")
    def revisar_meu_dia() -> str:
        return ("Chame planejar_dia. Em até 6 linhas, diga o que está atrasado, o que vence hoje e em quais horários "
                "livres encaixar cada prioridade. Pergunte antes de concluir ou criar qualquer tarefa.")

    @srv.prompt(description="Transformar um texto em tarefas revisadas.")
    def texto_para_tarefas(texto: str) -> str:
        return (f"Chame extrair_tarefas com o texto abaixo, mostre as tarefas em lista e pergunte quais criar.\n\n"
                f"{texto}")

    return srv


def _default_llm():
    from dotenv import load_dotenv
    load_dotenv(Path.home() / ".config" / "portfolio" / ".env")
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    from .llm import GeminiLLM
    return GeminiLLM(key, os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))


def main() -> None:
    ap = argparse.ArgumentParser(prog="copilot-mcp")
    ap.add_argument("--pasta", type=Path, default=Path.home() / "copiloto")
    ap.add_argument("--agenda", type=Path, default=None, help="arquivo .ics exportado do seu calendário")
    a = ap.parse_args()
    build_server(a.pasta, a.agenda).run("stdio")


if __name__ == "__main__":
    main()
