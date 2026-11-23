"""Sistema 2 - Agente único + roteamento.
Um roteador (saída estruturada) classifica a pergunta em sql | cypher | ambos | fora_do_escopo.
Cada especialista é o agente único com SÓ o seu esquema e as suas ferramentas.
  sql / cypher   -> o especialista responde.
  ambos          -> especialista do grafo -> especialista SQL (recebe o resultado do grafo) -> redator.
  fora_do_escopo -> recusa fixa, sem ferramenta.
Hipótese: menos contexto por agente melhora a consulta gerada."""
from typing import Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .comum import Sistema, construir_react, limpar, prompt_agente

PROMPT_ROTEADOR = """Você classifica perguntas sobre o Yelp de Santa Barbara em qual banco responde:
- "sql": atributos de negócios (nota, categoria, endereço, aberto/fechado, preço), contagens, médias, ordenações,
  reviews e usuários (contadores, datas, notas).
- "cypher": amizades, amigos de amigos, caminho entre usuários, quem avaliou os mesmos negócios que quem.
- "ambos": precisa das relações do grafo E de números/atributos do SQL (ex.: média das notas dadas pelos amigos de alguém).
- "fora_do_escopo": não tem relação com negócios/avaliações/usuários do Yelp em Santa Barbara (clima, notícias, etc.).
Pergunta sobre item que talvez não exista NÃO é fora do escopo: classifique pelo banco que o consultaria."""

RECUSA = ("Essa pergunta está fora do meu escopo: só consigo responder sobre negócios, avaliações e "
          "usuários do Yelp em Santa Barbara, CA.")


class Rota(BaseModel):
    banco: Literal["sql", "cypher", "ambos", "fora_do_escopo"] = Field(description="banco que responde a pergunta")
    motivo: str = Field(default="", description="justificativa curta")


class Estado(TypedDict, total=False):
    pergunta: str
    rota: str
    parcial: str
    resposta: str


class Roteamento(Sistema):
    nome = "roteamento"

    def __init__(self, ferramentas):
        super().__init__(ferramentas)
        self.roteador = self.llm.with_structured_output(Rota)
        ferr_sql = self.selecionar({"run_sql", "find_entity", "business_details", "compare_businesses"})
        ferr_cy = self.selecionar({"run_cypher", "find_entity"})
        self.esp_sql = construir_react(self.llm, ferr_sql, prompt_agente(sql=True, grafo=False))
        self.esp_cy = construir_react(self.llm, ferr_cy, prompt_agente(sql=False, grafo=True))
        self.grafo = self._montar()

    async def _rodar(self, agente, texto, config):
        res = await agente.ainvoke({"messages": [HumanMessage(texto)]}, config)
        return limpar(res["messages"][-1].content)

    def _montar(self):
        async def rotear(s, config):
            try:
                r = await self.roteador.ainvoke(
                    [SystemMessage(PROMPT_ROTEADOR), HumanMessage(s["pergunta"])], config)
                banco = r.banco
            except Exception:
                banco = "ambos"  # na dúvida, não bloqueia
            self.extras["rota"] = banco
            return {"rota": banco}

        async def n_sql(s, config):
            return {"resposta": await self._rodar(self.esp_sql, s["pergunta"], config)}

        async def n_cypher(s, config):
            return {"resposta": await self._rodar(self.esp_cy, s["pergunta"], config)}

        async def n_cypher_h(s, config):
            t = (f"Pergunta original: {s['pergunta']}\nSua tarefa: use SOMENTE o grafo para obter a parte de "
                 "relações (amizades/avaliações). Devolva os user_id/business_id e nomes encontrados, sem tentar "
                 "calcular notas ou contagens de reviews (isso é do SQL).")
            return {"parcial": await self._rodar(self.esp_cy, t, config)}

        async def n_sql_h(s, config):
            t = (f"Pergunta original: {s['pergunta']}\nResultado já obtido do grafo:\n{s['parcial']}\n"
                 "Sua tarefa: use o SQL (com esses ids) para obter os números/atributos que faltam e responda.")
            return {"resposta": await self._rodar(self.esp_sql, t, config)}

        async def redator(s, config):
            msg = await self.llm.ainvoke([
                SystemMessage("Redija a resposta final em português, curta e exata, para a pergunta, usando "
                              "somente os dados fornecidos. Não invente nada."),
                HumanMessage(f"Pergunta: {s['pergunta']}\nDados do grafo:\n{s['parcial']}\nDados do SQL:\n{s['resposta']}"),
            ], config)
            return {"resposta": limpar(msg.content)}

        async def recusa(s, config):
            return {"resposta": RECUSA}

        g = StateGraph(Estado)
        for nome, fn in [("rotear", rotear), ("sql", n_sql), ("cypher", n_cypher), ("cypher_h", n_cypher_h),
                         ("sql_h", n_sql_h), ("redator", redator), ("recusa", recusa)]:
            g.add_node(nome, fn)
        g.add_edge(START, "rotear")
        g.add_conditional_edges("rotear", lambda s: s["rota"], {
            "sql": "sql", "cypher": "cypher", "ambos": "cypher_h", "fora_do_escopo": "recusa"})
        g.add_edge("cypher_h", "sql_h")
        g.add_edge("sql_h", "redator")
        for fim in ("sql", "cypher", "redator", "recusa"):
            g.add_edge(fim, END)
        return g.compile()

    async def _responder(self, pergunta, config):
        res = await self.grafo.ainvoke({"pergunta": pergunta}, config)
        return res.get("resposta", "")
