"""Sistema 1 - Agente único: loop modelo <-> ferramentas (create_react_agent do LangGraph).
Recebe os dois esquemas e as 5 ferramentas. Erro de consulta volta como observação e o próprio modelo corrige."""
from langchain_core.messages import HumanMessage

from .comum import Sistema, construir_react, limpar, prompt_agente


class AgenteUnico(Sistema):
    nome = "unico"

    def __init__(self, ferramentas):
        super().__init__(ferramentas)
        self.agente = construir_react(self.llm, self.todas, prompt_agente(sql=True, grafo=True))

    async def _responder(self, pergunta, config):
        res = await self.agente.ainvoke({"messages": [HumanMessage(pergunta)]}, config)
        return limpar(res["messages"][-1].content)
