"""Sistema 3 - Agente único + verificador (gerador com verificador).
O agente único gera a resposta; o código reúne as consultas executadas e seus resultados; o verificador julga
(a) a consulta responde à pergunta (filtros, agregação, banco certo)? (b) números e nomes da resposta batem
com os resultados? Objeção só vale com evidência citada dos resultados; no máximo 2 devoluções.
NÃO olha sintaxe e NÃO contesta resultado vazio: sem consulta ou só com resultados vazios, aprova direto."""
from typing import TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .comum import Sistema, construir_react, extrair_consultas, limpar, prompt_agente, resultado_vazio

MAX_DEVOLUCOES = 2

PROMPT_VERIFICADOR = """Você é um revisor de respostas geradas a partir de consultas a bancos de dados.
Receba a pergunta, as consultas executadas (com resultados) e a resposta. Julgue SOMENTE:
(a) a consulta responde exatamente ao que foi perguntado (filtros, agregação, banco/tabela certos)?
(b) os números e nomes da resposta batem com os resultados das consultas?
REGRAS: NÃO avalie sintaxe. NÃO conteste resultado vazio. Só objete se puder citar, em "evidencia", o trecho
exato dos resultados que contradiz a resposta (ou o filtro ausente na consulta). Em dúvida, APROVE."""


class Veredito(BaseModel):
    aprovado: bool = Field(description="true se a resposta está correta e bem fundamentada")
    objecao: str = Field(default="", description="o que está errado, se não aprovou")
    evidencia: str = Field(default="", description="trecho dos resultados que sustenta a objeção")


class Estado(TypedDict, total=False):
    pergunta: str
    resposta: str
    consultas: list
    feedback: str
    devolucoes: int


class Verificador(Sistema):
    nome = "verificador"

    def __init__(self, ferramentas):
        super().__init__(ferramentas)
        self.agente = construir_react(self.llm, self.todas, prompt_agente(sql=True, grafo=True))
        self.verif = self.llm.with_structured_output(Veredito)
        self.grafo = self._montar()

    def _montar(self):
        async def gerar(s, config):
            msgs = [HumanMessage(s["pergunta"])]
            if s.get("feedback"):
                msgs += [AIMessage(content=s["resposta"]),
                         HumanMessage("Um revisor objetou: " + s["feedback"] +
                                      "\nCorrija usando as ferramentas e responda de novo.")]
            res = await self.agente.ainvoke({"messages": msgs}, config)
            return {"resposta": limpar(res["messages"][-1].content),
                    "consultas": extrair_consultas(res["messages"]), "feedback": ""}

        async def verificar(s, config):
            cons = s.get("consultas") or []
            self.extras.setdefault("devolucoes", 0)
            if not cons or all(resultado_vazio(c["saida"]) for c in cons):
                return {}  # sem consulta (recusa) ou só vazios: aprova
            linhas = []
            for c in cons:
                linhas.append("[" + c["ferramenta"] + "] args=" + str(c["args"]) + "\nresultado=" + c["saida"][:1500])
            try:
                v = await self.verif.ainvoke([
                    SystemMessage(PROMPT_VERIFICADOR),
                    HumanMessage("PERGUNTA: " + s["pergunta"] + "\n\nCONSULTAS E RESULTADOS:\n" + "\n".join(linhas)
                                 + "\n\nRESPOSTA: " + s["resposta"]),
                ], config)
            except Exception:
                return {}
            self.extras["verificador_chamadas"] = self.extras.get("verificador_chamadas", 0) + 1
            if v.aprovado or not v.evidencia.strip() or s.get("devolucoes", 0) >= MAX_DEVOLUCOES:
                return {}
            self.extras["devolucoes"] += 1
            return {"feedback": v.objecao + " Evidência: " + v.evidencia, "devolucoes": s.get("devolucoes", 0) + 1}

        g = StateGraph(Estado)
        g.add_node("gerar", gerar)
        g.add_node("verificar", verificar)
        g.add_edge(START, "gerar")
        g.add_edge("gerar", "verificar")
        g.add_conditional_edges("verificar", lambda s: "gerar" if s.get("feedback") else END,
                                {"gerar": "gerar", END: END})
        return g.compile()

    async def _responder(self, pergunta, config):
        res = await self.grafo.ainvoke({"pergunta": pergunta, "devolucoes": 0}, config)
        return res.get("resposta", "")
