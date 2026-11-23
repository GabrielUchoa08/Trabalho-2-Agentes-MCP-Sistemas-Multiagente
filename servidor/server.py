"""Servidor MCP (FastMCP) - Yelp Santa Barbara.

5 ferramentas : run_sql, run_cypher, find_entity, business_details, compare_businesses
2 resources   : schema://sql, schema://graph
1 prompt      : analisar_negocio

Todo acesso a banco fica aqui dentro. Somente leitura:
  - PostgreSQL: usuário 'leitor' (só SELECT) + transação read-only + statement_timeout
  - Neo4j: transação de leitura + bloqueio de palavras de escrita + timeout
Erros voltam como dict {"ok": False, "erro": ..., "dica": ...} para o modelo se recuperar.

Execução:
  python servidor/server.py                      # stdio
  python servidor/server.py --http --port 8765   # HTTP em http://127.0.0.1:8765/mcp
"""
import argparse
import os
import re
from datetime import date, datetime
from decimal import Decimal

import psycopg2
from fastmcp import FastMCP
from neo4j import GraphDatabase, Query
from neo4j.exceptions import CypherSyntaxError, Neo4jError

from esquemas import ESQUEMA_GRAFO, ESQUEMA_SQL

MAX_LINHAS = 30
TIMEOUT_PG_MS = 10_000
TIMEOUT_NEO_S = 15

mcp = FastMCP("yelp-santa-barbara")


# ----------------------------------------------------------------- utilitários
def _j(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _sem_literais(q: str) -> str:
    """Remove strings entre aspas e comentários, para checar palavras proibidas."""
    q = re.sub(r"'(?:[^']|'')*'", "''", q)
    q = re.sub(r'"(?:[^"]|"")*"', '""', q)
    q = re.sub(r"--[^\n]*", " ", q)
    q = re.sub(r"//[^\n]*", " ", q)
    return re.sub(r"/\*.*?\*/", " ", q, flags=re.S)


def _erro(msg: str, dica: str = "") -> dict:
    d = {"ok": False, "erro": msg}
    if dica:
        d["dica"] = dica
    return d


# ------------------------------------------------------------------ PostgreSQL
def _pg():
    return psycopg2.connect(
        dbname=os.getenv("PG_DB", "yelp"),
        user=os.getenv("PG_USER", "leitor"),
        password=os.getenv("PG_PASS", "leitor"),
        host=os.getenv("PG_HOST", "localhost"),
        options=f"-c default_transaction_read_only=on -c statement_timeout={TIMEOUT_PG_MS}",
    )


def _sql(q: str, params=None, limite: int = MAX_LINHAS):
    con = _pg()
    try:
        with con.cursor() as cur:
            cur.execute(q, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(limite + 1)
    finally:
        con.close()
    truncado = len(rows) > limite
    return cols, [{c: _j(v) for c, v in zip(cols, r)} for r in rows[:limite]], truncado


def _tabelas_colunas() -> str:
    _, rows, _ = _sql(
        "SELECT table_name AS t, string_agg(column_name, ', ' ORDER BY ordinal_position) AS c "
        "FROM information_schema.columns WHERE table_schema='public' GROUP BY table_name ORDER BY 1"
    )
    return "; ".join(f"{r['t']}({r['c']})" for r in rows)


_ESCRITA_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|vacuum|reindex|comment)\b", re.I
)


@mcp.tool()
def run_sql(query: str) -> dict:
    """Executa UMA consulta SELECT (ou WITH ... SELECT) no PostgreSQL (somente leitura) e devolve
    colunas e linhas (no máximo 30; o campo 'truncado' avisa se havia mais). Consulte o resource
    schema://sql para ver tabelas e colunas. Use para filtros, contagens, médias, ordenações."""
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        return _erro("Consulta vazia.", "Envie um SELECT. Ex.: SELECT COUNT(*) FROM business")
    if ";" in q:
        return _erro("Mais de um comando na mesma chamada.", "Envie uma única consulta por chamada, sem ';' no meio.")
    if not re.match(r"(?is)^\s*(select|with)\b", q):
        return _erro("Só são permitidas consultas que começam com SELECT ou WITH.")
    if _ESCRITA_SQL.search(_sem_literais(q)):
        return _erro("Comando de escrita bloqueado: o banco é somente leitura.", "Use apenas SELECT.")
    if re.search(r"\b(from|join)\s+user\b", _sem_literais(q), re.I):
        # no PostgreSQL 'FROM user' NÃO dá erro: devolve o nome do usuário da sessão (resultado falso)
        return _erro("'user' é palavra reservada do PostgreSQL (devolve o usuário da sessão, não a tabela).",
                     "A tabela de usuários se chama users: FROM users.")
    try:
        cols, linhas, trunc = _sql(q)
        return {"ok": True, "colunas": cols, "linhas": linhas, "total_linhas": len(linhas), "truncado": trunc}
    except psycopg2.OperationalError as e:
        if getattr(e, "pgcode", None) is None:  # sem conexão: não é culpa da consulta
            return _erro(f"Banco PostgreSQL indisponível: {(str(e).strip().splitlines() or [''])[0]}",
                         "Falha de infraestrutura, não da consulta. Não adianta reescrever; informe que o banco está indisponível.")
        return _erro((str(e).strip().splitlines() or ["erro"])[0], "Revise a consulta.")
    except psycopg2.Error as e:
        msg = (str(e).strip().splitlines() or ["erro desconhecido"])[0]
        code = getattr(e, "pgcode", None)
        try:
            if code == "42P01":
                dica = f"Tabela inexistente. Tabelas e colunas válidas: {_tabelas_colunas()}. A tabela de usuários se chama users."
            elif code == "42703":
                dica = f"Coluna inexistente. Tabelas e colunas válidas: {_tabelas_colunas()}."
            elif code == "57014":
                dica = "Consulta demorou demais e foi cancelada. Adicione filtros, evite produto cartesiano, use LIMIT."
            elif code == "42601":
                dica = "Erro de sintaxe PostgreSQL. Revise vírgulas, aspas simples nos textos e nomes de colunas. A tabela de usuários se chama users (user é palavra reservada)."
            elif code in ("42883", "42804", "22P02"):
                dica = "Tipos incompatíveis ou função inexistente. Confira os tipos das colunas (ex.: is_open é 0/1)."
            else:
                dica = "Revise a consulta com base no esquema."
        except Exception:
            dica = "Revise a consulta com base no esquema."
        return _erro(msg, dica)
    except Exception as e:  # conexão etc.
        return _erro(f"Falha ao acessar o PostgreSQL: {e}")


# ----------------------------------------------------------------------- Neo4j
_driver = None


def _neo():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASS", "senha12345")),
        )
    return _driver


_ESCRITA_CYPHER = re.compile(r"\b(create|merge|delete|detach|set|remove|drop|foreach|call|load)\b", re.I)


@mcp.tool()
def run_cypher(query: str) -> dict:
    """Executa UMA consulta Cypher de LEITURA no Neo4j e devolve as linhas (no máximo 30).
    O grafo tem apenas (:User {user_id,name}), (:Business {business_id,name}), [:FRIEND] e [:REVIEWED].
    Consulte o resource schema://graph. Use para amigos, amigos de amigos, caminhos mais curtos e
    quem avaliou quais negócios. Notas, categorias e contagens de reviews ficam no SQL."""
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        return _erro("Consulta vazia.", "Ex.: MATCH (u:User {name: 'X'})-[:FRIEND]-(f) RETURN count(f) AS amigos")
    if ";" in q:
        return _erro("Mais de um comando na mesma chamada.", "Envie uma única consulta, sem ';'.")
    if _ESCRITA_CYPHER.search(_sem_literais(q)):
        return _erro(
            "Comando de escrita (ou CALL/LOAD) bloqueado: o grafo é somente leitura.",
            "Use somente MATCH, WHERE, WITH, RETURN, ORDER BY, LIMIT.",
        )

    def trabalho(tx):
        res = tx.run(Query(q, timeout=TIMEOUT_NEO_S))
        linhas = []
        for i, r in enumerate(res):
            if i > MAX_LINHAS:
                break
            linhas.append(r.data())
        return linhas

    try:
        with _neo().session() as s:
            linhas = s.execute_read(trabalho)
        trunc = len(linhas) > MAX_LINHAS
        linhas = [{k: _j(v) for k, v in l.items()} for l in linhas[:MAX_LINHAS]]
        return {"ok": True, "linhas": linhas, "total_linhas": len(linhas), "truncado": trunc}
    except CypherSyntaxError as e:
        return _erro(
            f"Erro de sintaxe Cypher: {e.message if hasattr(e, 'message') else e}",
            "Revise a sintaxe (Cypher 5). Rótulos: User, Business. Relações: FRIEND (sem direção), REVIEWED "
            "(User->Business). Propriedades: user_id, business_id, name.",
        )
    except Neo4jError as e:
        return _erro(
            f"Erro do Neo4j: {getattr(e, 'message', str(e))}",
            "Confira rótulos/propriedades do esquema; se for timeout, use LIMIT ou filtre por user_id.",
        )
    except Exception as e:
        return _erro(f"Falha ao acessar o Neo4j: {e}")


# ------------------------------------------------------------- busca e detalhes
@mcp.tool()
def find_entity(name: str, kind: str = "negocio") -> dict:
    """Busca aproximada (sem diferenciar maiúsculas) por NOME e devolve ids.
    kind: 'negocio' (retorna business_id), 'usuario' (retorna user_id) ou 'categoria' (nomes de categoria
    em inglês e quantidade de negócios). Zero resultados NÃO é erro: significa que não existe no recorte
    de Santa Barbara. Nomes repetidos (redes, homônimos) aparecem como vários resultados."""
    kinds = ("negocio", "usuario", "categoria")
    if kind not in kinds:
        return _erro(f"kind inválido: '{kind}'.", f"Use um destes: {', '.join(kinds)}.")
    nome = (name or "").strip()
    if len(nome) < 2:
        return _erro("Informe um nome com pelo menos 2 caracteres.")
    padrao = f"%{nome}%"
    try:
        if kind == "negocio":
            _, rows, _ = _sql(
                "SELECT business_id, name, address, stars, review_count, is_open FROM business "
                "WHERE name ILIKE %s ORDER BY (lower(name) = lower(%s)) DESC, review_count DESC LIMIT 10",
                (padrao, nome),
            )
        elif kind == "usuario":
            _, rows, _ = _sql(
                "SELECT user_id, name, review_count FROM users WHERE name ILIKE %s "
                "ORDER BY (lower(name) = lower(%s)) DESC, review_count DESC LIMIT 10",
                (padrao, nome),
            )
        else:
            _, rows, _ = _sql(
                "SELECT category, COUNT(DISTINCT business_id) AS negocios FROM business_category "
                "WHERE category ILIKE %s GROUP BY category ORDER BY negocios DESC LIMIT 10",
                (padrao,),
            )
    except Exception as e:
        return _erro(f"Falha na busca: {e}")
    res = {"ok": True, "tipo": kind, "encontrados": len(rows), "resultados": rows}
    if not rows:
        res["mensagem"] = f"Nenhum(a) {kind} com '{nome}' no recorte de Santa Barbara."
    nomes = [r.get("name") for r in rows if r.get("name")]
    if len(nomes) != len(set(n.lower() for n in nomes)):
        res["aviso"] = "Há nomes repetidos entre os resultados (homônimos/redes): escolha pelo id/endereço."
    return res


@mcp.tool()
def business_details(business_id: str) -> dict:
    """Todos os atributos de UM negócio pelo business_id (obtido em find_entity): endereço, CEP, nota,
    contagens, faixa de preço (pode ser nula), se está aberto, categorias, e a contagem/média de
    estrelas calculadas sobre as reviews de Santa Barbara."""
    bid = (business_id or "").strip()
    if not bid:
        return _erro("business_id vazio.", "Obtenha o id com find_entity(name, 'negocio').")
    try:
        _, rows, _ = _sql("SELECT * FROM business WHERE business_id = %s", (bid,))
        if not rows:
            return _erro(f"Negócio '{bid}' não encontrado.", "O id deve vir de find_entity(name, 'negocio'); não use o nome como id.")
        _, cats, _ = _sql("SELECT category FROM business_category WHERE business_id = %s ORDER BY category", (bid,))
        _, st, _ = _sql(
            "SELECT COUNT(*) AS reviews_no_recorte, ROUND(AVG(stars)::numeric, 2) AS media_estrelas_reviews_no_recorte "
            "FROM review WHERE business_id = %s",
            (bid,),
        )
    except Exception as e:
        return _erro(f"Falha ao buscar o negócio: {e}")
    d = rows[0]
    d["categorias"] = [c["category"] for c in cats]
    d.update(st[0])
    return {"ok": True, "negocio": d}


_CRITERIOS = {  # critério -> (coluna/alias, melhor = max|min)
    "stars": "max",
    "review_count": "max",
    "checkin_count": "max",
    "price_range": "min",  # menor = mais barato
    "media_estrelas_reviews": "max",
}


@mcp.tool()
def compare_businesses(ids: list[str]) -> dict:
    """Compara de 2 a 5 negócios lado a lado (ids = lista de business_id vindos de find_entity) e
    CALCULA o melhor por critério: stars (maior), review_count (maior), checkin_count (maior),
    price_range (menor = mais barato, ignora nulos) e media_estrelas_reviews (maior; média das reviews
    de Santa Barbara). Empates listam todos os negócios empatados."""
    if not isinstance(ids, list):
        return _erro("'ids' deve ser uma lista de business_id.", "Ex.: ids=['abc123', 'def456']")
    ids = list(dict.fromkeys(str(i).strip() for i in ids if str(i).strip()))
    if not 2 <= len(ids) <= 5:
        return _erro(f"Envie de 2 a 5 ids distintos (recebi {len(ids)}).", "Use find_entity(name, 'negocio') para obter os ids.")
    try:
        _, rows, _ = _sql(
            "SELECT b.business_id, b.name, b.stars, b.review_count, b.checkin_count, b.price_range, b.is_open, "
            "(SELECT ROUND(AVG(r.stars)::numeric, 2) FROM review r WHERE r.business_id = b.business_id) AS media_estrelas_reviews "
            "FROM business b WHERE b.business_id = ANY(%s)",
            (ids,),
        )
    except Exception as e:
        return _erro(f"Falha na comparação: {e}")
    achados = {r["business_id"] for r in rows}
    faltando = [i for i in ids if i not in achados]
    if faltando:
        return _erro(f"Ids inexistentes: {faltando}.", "Obtenha ids válidos com find_entity(name, 'negocio'); não use o nome como id.")
    melhor = {}
    for crit, modo in _CRITERIOS.items():
        validos = [r for r in rows if r.get(crit) is not None]
        if not validos:
            melhor[crit] = {"valor": None, "negocios": [], "nota": "sem dados"}
            continue
        alvo = (max if modo == "max" else min)(r[crit] for r in validos)
        melhor[crit] = {"valor": alvo, "negocios": [r["name"] for r in validos if r[crit] == alvo]}
    return {"ok": True, "negocios": rows, "melhor_por_criterio": melhor}


# ------------------------------------------------------------ resources/prompt
@mcp.resource("schema://sql")
def schema_sql() -> str:
    """Esquema do banco relacional (PostgreSQL)."""
    return ESQUEMA_SQL


@mcp.resource("schema://graph")
def schema_graph() -> str:
    """Esquema do banco de grafos (Neo4j)."""
    return ESQUEMA_GRAFO


@mcp.prompt()
def analisar_negocio(nome: str) -> str:
    """Pedido padrão: análise completa de um negócio pelo nome."""
    return (
        f"Faça uma análise do negócio '{nome}' em Santa Barbara: 1) localize-o com find_entity (se houver "
        "homônimos, liste-os e peça para escolher pelo endereço); 2) traga os detalhes com business_details; "
        "3) diga a nota, o número de reviews, se está aberto e as categorias; 4) se houver outros negócios "
        "da mesma categoria bem avaliados, compare com compare_businesses. Responda em português."
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    if a.http:
        # Se sua versão do FastMCP só aceitar "streamable-http", troque o transporte abaixo.
        mcp.run(transport="http", host=a.host, port=a.port)
    else:
        mcp.run()
