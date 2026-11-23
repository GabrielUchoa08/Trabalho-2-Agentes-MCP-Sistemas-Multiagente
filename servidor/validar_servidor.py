"""Validação do servidor MCP SEM LLM. Gera resultados/validacao_mcp.json como evidência.

Uso (no ambiente do servidor):
  python servidor/validar_servidor.py                          # sobe o servidor por stdio
  python servidor/validar_servidor.py http://127.0.0.1:8765/mcp # usa o servidor HTTP já rodando
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from fastmcp import Client

ALVO = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
# caminho -> Path (stdio); URL -> string (HTTP)
ALVO_CLIENTE = ALVO if ALVO.startswith("http") else Path(ALVO)
SAIDA = os.path.join(os.path.dirname(__file__), "..", "resultados", "validacao_mcp.json")
OBRIGATORIAS = {"run_sql", "run_cypher", "find_entity", "business_details", "compare_businesses"}


def dados(res):
    sc = getattr(res, "structured_content", None)
    if sc:
        return sc["result"] if list(sc) == ["result"] else sc
    return json.loads(res.content[0].text)


async def main():
    evid, falhas = [], 0

    def check(nome, cond, detalhe=None):
        nonlocal falhas
        evid.append({"teste": nome, "passou": bool(cond), "detalhe": detalhe})
        print(("OK   " if cond else "FALHA"), nome)
        falhas += 0 if cond else 1

    async with Client(ALVO_CLIENTE) as c:
        tools = await c.list_tools()
        nomes = {t.name for t in tools}
        check("lista >= 4 ferramentas com as 5 esperadas", OBRIGATORIAS <= nomes, sorted(nomes))
        res = await c.list_resources()
        uris = {str(r.uri) for r in res}
        check("resources schema://sql e schema://graph", {"schema://sql", "schema://graph"} <= uris, sorted(uris))
        for u in ("schema://sql", "schema://graph"):
            conteudo = await c.read_resource(u)
            check(f"ler {u}", len(conteudo[0].text) > 100)
        prompts = await c.list_prompts()
        check("existe >= 1 prompt", len(prompts) >= 1, [p.name for p in prompts])
        p = await c.get_prompt("analisar_negocio", {"nome": "Teste"})
        check("renderizar prompt analisar_negocio", "Teste" in p.messages[0].content.text)

        async def chamar(nome, args):
            return dados(await c.call_tool(nome, args))

        r = await chamar("run_sql", {"query": "SELECT COUNT(*) AS n FROM business"})
        check("run_sql conta negócios", r.get("ok") and r["linhas"][0]["n"] > 0, r)
        r = await chamar("run_cypher", {"query": "MATCH (u:User) RETURN count(u) AS n"})
        check("run_cypher conta usuários", r.get("ok") and r["linhas"][0]["n"] > 0, r)
        r = await chamar("find_entity", {"name": "Mexican", "kind": "categoria"})
        check("find_entity categoria", r.get("ok") and r["encontrados"] >= 1, r)
        r2 = await chamar("find_entity", {"name": "Cantina do Pantano Voador", "kind": "negocio"})
        check("find_entity inexistente devolve vazio (não é erro)", r2.get("ok") and r2["encontrados"] == 0, r2)
        b = await chamar("run_sql", {"query": "SELECT business_id FROM business ORDER BY review_count DESC LIMIT 3"})
        ids = [x["business_id"] for x in b["linhas"]]
        d = await chamar("business_details", {"business_id": ids[0]})
        check("business_details", d.get("ok") and "categorias" in d["negocio"], d)
        cmp_ = await chamar("compare_businesses", {"ids": ids})
        check("compare_businesses calcula melhor por critério", cmp_.get("ok") and "stars" in cmp_["melhor_por_criterio"], cmp_)

        # --- erros claros (o modelo precisa conseguir se recuperar) ---
        casos = [
            ("run_sql escrita bloqueada", "run_sql", {"query": "DELETE FROM review"}),
            ("run_sql tabela inexistente traz dica com tabelas válidas", "run_sql", {"query": "SELECT * FROM usuarios"}),
            ("run_sql palavra reservada user dá dica sobre users", "run_sql", {"query": "SELECT * FROM user"}),
            ("run_sql coluna inexistente traz dica", "run_sql", {"query": "SELECT estrelas FROM business"}),
            ("run_sql dois comandos", "run_sql", {"query": "SELECT 1; SELECT 2"}),
            ("run_cypher escrita bloqueada", "run_cypher", {"query": "MATCH (n) DETACH DELETE n"}),
            ("run_cypher sintaxe inválida", "run_cypher", {"query": "MATCH (u:User RETURN u"}),
            ("find_entity kind inválido", "find_entity", {"name": "abc", "kind": "pessoa"}),
            ("business_details id inexistente", "business_details", {"business_id": "nao_existe"}),
            ("compare_businesses com 1 id", "compare_businesses", {"ids": ["x"]}),
            ("compare_businesses id inexistente", "compare_businesses", {"ids": ["x", "y"]}),
        ]
        for nome, tool, args in casos:
            r = await chamar(tool, args)
            check(nome, r.get("ok") is False and r.get("erro"), r)
        # o banco realmente não mudou
        r = await chamar("run_sql", {"query": "SELECT COUNT(*) AS n FROM review"})
        check("review continua intacta após tentativa de DELETE", r["linhas"][0]["n"] > 0, r)

    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump({"alvo": ALVO, "falhas": falhas, "testes": evid}, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n{len(evid) - falhas}/{len(evid)} testes passaram. Evidência em {os.path.abspath(SAIDA)}")
    sys.exit(1 if falhas else 0)


asyncio.run(main())
