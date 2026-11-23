"""Juiz de fronteira (Gemini, API gratuita do Google AI Studio) com acesso SOMENTE LEITURA ao servidor MCP.
Para cada linha de resultados/resultados.csv sem veredito: recebe pergunta, resposta, gabarito e nota; pode consultar
os bancos via run_sql / run_cypher (as duas ferramentas do MCP são somente leitura); devolve veredito + justificativa
e registra as consultas feitas. Salva a cada julgamento e retoma de onde parou.

  export GOOGLE_API_KEY=...        (no Colab: userdata.get("GOOGLE_API_KEY"))
  python -m avaliacao.juiz
Modelo: JUIZ_MODELO (padrão gemini-2.5-flash). Ajuste PAUSA_S se bater no limite de requisições da camada gratuita."""
import json
import os
import re
import time

import pandas as pd
from google import genai
from google.genai import types

from agentes.comum import carregar_ferramentas, chamar_ferramenta

MODELO_JUIZ = os.getenv("JUIZ_MODELO", "gemini-2.5-flash")
PAUSA_S = float(os.getenv("PAUSA_S", "6"))
CSV = "resultados/resultados.csv"

SISTEMA = """Você é um juiz rigoroso e imparcial. Avalia se a RESPOSTA de um assistente está correta para a PERGUNTA,
sobre o Yelp de Santa Barbara, CA (PostgreSQL + Neo4j). Você recebe o GABARITO e a consulta de referência, mas pode e
deve conferir nos bancos com run_sql e run_cypher (somente leitura) quando houver dúvida.
Critérios: números e nomes corretos (arredondamentos pequenos são aceitáveis); a resposta deve responder o que foi
perguntado; não pode inventar dados. Pergunta fora do escopo: correto = recusar sem inventar. Item inexistente:
correto = dizer que não existe. Pergunta capciosa: siga a NOTA. Não penalize estilo nem tamanho.
Termine SEMPRE com um único JSON em uma linha: {"veredito": "correto" ou "incorreto", "justificativa": "..."}"""


def main():
    ferr = carregar_ferramentas()
    consultas = []

    def run_sql(query: str) -> str:
        """Executa um SELECT somente leitura no PostgreSQL e devolve JSON."""
        consultas.append(("sql", query))
        return chamar_ferramenta(ferr, "run_sql", {"query": query})

    def run_cypher(query: str) -> str:
        """Executa uma consulta Cypher somente leitura no Neo4j e devolve JSON."""
        consultas.append(("cypher", query))
        return chamar_ferramenta(ferr, "run_cypher", {"query": query})

    cliente = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    cfg = types.GenerateContentConfig(system_instruction=SISTEMA, tools=[run_sql, run_cypher], temperature=0)
    with open("avaliacao/perguntas.json", encoding="utf-8") as f:
        perguntas = {p["id"]: p for p in json.load(f)}

    df = pd.read_csv(CSV, dtype=str).fillna("")
    for col in ("juiz_veredito", "juiz_justificativa", "juiz_consultas"):
        if col not in df:
            df[col] = ""
    for i, row in df.iterrows():
        if row["juiz_veredito"]:
            continue
        q = perguntas[row["id"]]
        prompt = (f"PERGUNTA: {q['pergunta']}\nCATEGORIA: {q['categoria']}\n"
                  f"GABARITO: {json.dumps(q['gabarito'], ensure_ascii=False, default=str)}\n"
                  f"CONSULTA DE REFERÊNCIA: {q['consulta_ref']}\nNOTA: {q.get('nota_juiz', '')}\n\n"
                  f"RESPOSTA DO ASSISTENTE:\n{row['resposta'] or '(vazia)'}")
        consultas.clear()
        texto = ""
        for tentativa in range(5):
            try:
                texto = cliente.models.generate_content(model=MODELO_JUIZ, contents=prompt, config=cfg).text or ""
                break
            except Exception as e:  # limite de taxa etc.
                print("  juiz: erro", str(e)[:120], "- aguardando")
                time.sleep(30 * (tentativa + 1))
        achados = re.findall(r"\{[^{}]*\"veredito\"[^{}]*\}", texto, flags=re.S)
        try:
            j = json.loads(achados[-1])
            ver, just = j["veredito"].strip().lower(), j.get("justificativa", "")
        except Exception:
            ver, just = "erro_juiz", texto[:500]
        df.loc[i, ["juiz_veredito", "juiz_justificativa", "juiz_consultas"]] = [
            ver, just, json.dumps(consultas, ensure_ascii=False)]
        df.to_csv(CSV, index=False)
        print(f"{row['id']} {row['sistema']:<11} -> {ver}")
        time.sleep(PAUSA_S)


if __name__ == "__main__":
    main()
