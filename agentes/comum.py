"""Código comum aos 3 sistemas: modelo, ferramentas MCP, métricas e a interface answer(question)."""
import asyncio
import json
import os
import re
import subprocess
import threading
import time

import nest_asyncio
import psutil
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: F401
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama

from servidor.esquemas import ESQUEMA_GRAFO, ESQUEMA_SQL

nest_asyncio.apply()  # permite asyncio.run dentro do Jupyter/Colab

MODELO = os.getenv("MODELO", "qwen3:8b")
MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8765/mcp")
NUM_CTX = int(os.getenv("NUM_CTX", "8192"))
RECURSION_LIMIT = 40

REGRAS = """\
REGRAS:
- Responda em português, de forma curta e objetiva, com os números e nomes exatos vindos das consultas.
- NUNCA invente dados: todo número ou nome da resposta deve vir do resultado de uma ferramenta.
- Se uma ferramenta devolver "ok": false, leia o erro e a dica, corrija a chamada e tente de novo.
- Resultado vazio ou find_entity com 0 encontrados significa que o item NÃO EXISTE no recorte: diga isso e pare, sem inventar nem insistir.
- Se a pergunta não for sobre negócios, avaliações ou usuários do Yelp em Santa Barbara, recuse educadamente SEM chamar ferramenta.
- Valores de categoria estão em inglês (mexicano = 'Mexican'; cafés = 'Coffee & Tea'); use find_entity(nome, 'categoria') se tiver dúvida.
- users.review_count/average_stars contam o Yelp inteiro; para o recorte use a tabela review. Ao responder sobre isso, explique qual contagem usou.
- Nomes de usuário e de negócio podem se repetir: se houver homônimos relevantes, diga.
- Use o mínimo de chamadas necessário.
"""


def prompt_agente(sql: bool = True, grafo: bool = True) -> str:
    partes = ["Você é um assistente que responde perguntas sobre o Yelp de Santa Barbara, CA, usando as ferramentas disponíveis."]
    if sql:
        partes.append(ESQUEMA_SQL)
    if grafo:
        partes.append(ESQUEMA_GRAFO)
    partes.append(REGRAS)
    return "\n".join(partes)


def criar_llm() -> ChatOllama:
    # reasoning=False desliga o "thinking" do qwen3 (mais rápido). Se sua versão do
    # langchain-ollama não aceitar o parâmetro, remova-o e acrescente /no_think nos prompts.
    return ChatOllama(model=MODELO, temperature=0, num_ctx=NUM_CTX, reasoning=False)


def construir_react(llm, ferramentas, prompt):
    from langgraph.prebuilt import create_react_agent
    return create_react_agent(llm, ferramentas, prompt=prompt)


# ---------------------------------------------------------------- ferramentas
async def _carregar():
    client = MultiServerMCPClient({"yelp": {"url": MCP_URL, "transport": "streamable_http"}})
    return await client.get_tools()


def carregar_ferramentas():
    return asyncio.run(_carregar())


def chamar_ferramenta(ferramentas, nome, args) -> str:
    """Chamada direta (usada pelo juiz, com acesso somente leitura)."""
    ferr = {t.name: t for t in ferramentas}
    return texto(asyncio.run(ferr[nome].ainvoke(args)))


# ------------------------------------------------------------------ utilidades
def texto(x) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if hasattr(x, "content"):
        return texto(x.content)
    if isinstance(x, (list, tuple)):
        return "\n".join(texto(i) for i in x)
    if isinstance(x, dict):
        if "text" in x:
            return str(x["text"])
        return json.dumps(x, ensure_ascii=False, default=str)
    return str(x)


def limpar(resposta) -> str:
    t = texto(resposta)
    return re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()


def extrair_consultas(msgs) -> list[dict]:
    saidas = {m.tool_call_id: texto(m.content) for m in msgs if isinstance(m, ToolMessage)}
    out = []
    for m in msgs:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                out.append({"ferramenta": tc["name"], "args": tc["args"], "saida": saidas.get(tc["id"], "")})
    return out


def resultado_vazio(saida: str) -> bool:
    try:
        d = json.loads(saida)
    except Exception:
        return False
    if not isinstance(d, dict) or d.get("ok") is False:
        return False
    if "linhas" in d:
        return not d["linhas"]
    if "resultados" in d:
        return not d["resultados"]
    return False


# --------------------------------------------------------------------- métricas
class Metricas(BaseCallbackHandler):
    """Soma chamadas ao modelo, tokens e chamadas de ferramenta, em TODOS os agentes/nós do grafo."""

    def __init__(self):
        self.chamadas_modelo = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.ferramentas: list[str] = []
        self._pend: dict = {}
        self.trajetoria: list[dict] = []

    def on_llm_end(self, response, **kw):
        self.chamadas_modelo += 1
        for gens in response.generations:
            for g in gens:
                um = getattr(getattr(g, "message", None), "usage_metadata", None)
                if um:
                    self.tokens_in += um.get("input_tokens", 0)
                    self.tokens_out += um.get("output_tokens", 0)

    def on_tool_start(self, serialized, input_str, *, run_id, **kw):
        nome = (serialized or {}).get("name") or kw.get("name") or "?"
        self.ferramentas.append(nome)
        self._pend[run_id] = {"ferramenta": nome, "args": kw.get("inputs") or input_str}

    def on_tool_end(self, output, *, run_id, **kw):
        d = self._pend.pop(run_id, {"ferramenta": "?", "args": ""})
        d["saida"] = texto(output)[:1500]
        self.trajetoria.append(d)

    def on_tool_error(self, error, *, run_id, **kw):
        d = self._pend.pop(run_id, {"ferramenta": "?", "args": ""})
        d["saida"] = f"EXCEÇÃO: {error}"
        self.trajetoria.append(d)


class Amostrador(threading.Thread):
    """CPU e RAM do sistema (psutil) e GPU (nvidia-smi) amostrados durante a execução."""

    def __init__(self, intervalo=0.5):
        super().__init__(daemon=True)
        self.intervalo = intervalo
        self._parar = threading.Event()
        self.cpu, self.ram, self.gpu_u, self.gpu_m = [], [], [], []

    def run(self):
        psutil.cpu_percent(None)
        while not self._parar.is_set():
            self.cpu.append(psutil.cpu_percent(None))
            self.ram.append(psutil.virtual_memory().used / 2**20)
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3,
                ).stdout.strip().splitlines()[0]
                u, m = [float(x) for x in out.split(",")]
                self.gpu_u.append(u)
                self.gpu_m.append(m)
            except Exception:
                pass
            self._parar.wait(self.intervalo)

    def parar(self):
        self._parar.set()
        self.join(timeout=3)

    @staticmethod
    def _med(v):
        return round(sum(v) / len(v), 2) if v else None

    def resumo(self):
        return {
            "cpu_medio": self._med(self.cpu), "cpu_max": max(self.cpu) if self.cpu else None,
            "ram_mb_medio": self._med(self.ram), "ram_mb_max": round(max(self.ram), 1) if self.ram else None,
            "gpu_util_medio": self._med(self.gpu_u), "gpu_util_max": max(self.gpu_u) if self.gpu_u else None,
            "gpu_mem_mb_max": max(self.gpu_m) if self.gpu_m else None,
        }


# ---------------------------------------------------------------- classe base
class Sistema:
    """Interface comum: answer(pergunta) -> dict com resposta e dados de execução."""

    nome = "base"

    def __init__(self, ferramentas):
        self.todas = ferramentas
        self.llm = criar_llm()
        self.extras: dict = {}

    def selecionar(self, nomes):
        return [t for t in self.todas if t.name in nomes]

    async def _responder(self, pergunta: str, config: dict) -> str:  # implementado pelas subclasses
        raise NotImplementedError

    def answer(self, pergunta: str) -> dict:
        return asyncio.run(self.aanswer(pergunta))

    async def aanswer(self, pergunta: str) -> dict:
        m, amo = Metricas(), Amostrador()
        self.extras = {}
        amo.start()
        t0 = time.perf_counter()
        resposta, erro = "", None
        try:
            resposta = await self._responder(pergunta, {"callbacks": [m], "recursion_limit": RECURSION_LIMIT})
        except Exception as e:  # inclui GraphRecursionError
            erro = f"{type(e).__name__}: {e}"
        seg = round(time.perf_counter() - t0, 3)
        amo.parar()
        return {
            "sistema": self.nome,
            "resposta": resposta,
            "erro": erro,
            "ferramentas_chamadas": m.ferramentas,
            "chamadas_modelo": m.chamadas_modelo,
            "chamadas_ferramenta": len(m.ferramentas),
            "tokens_in": m.tokens_in,
            "tokens_out": m.tokens_out,
            "segundos": seg,
            **amo.resumo(),
            "trajetoria": m.trajetoria,
            "extras": self.extras,
        }
