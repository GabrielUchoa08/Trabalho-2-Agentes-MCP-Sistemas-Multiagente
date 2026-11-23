"""Carrega os CSV do recorte (Santa Barbara) no PostgreSQL e no Neo4j.

  python dados/carregar_bancos.py --dir /content/drive/MyDrive/yelp_recorte

Arquivos esperados em --dir: business.csv, business_category.csv, users.csv, review.csv,
friends.csv, reviewed.csv. Idempotente: recria tabelas e esvazia o grafo antes de carregar.
"""
import argparse
import io
import time

import pandas as pd
import psycopg2
from neo4j import GraphDatabase

PG_ADMIN = dict(dbname="yelp", user="postgres", password="postgres", host="localhost")
NEO_URI, NEO_AUTH = "bolt://localhost:7687", ("neo4j", "senha12345")

COLS = {
    "business": ["business_id", "name", "address", "postal_code", "latitude", "longitude",
                 "stars", "review_count", "is_open", "price_range", "checkin_count"],
    "business_category": ["business_id", "category"],
    "users": ["user_id", "name", "review_count", "yelping_since", "useful", "funny", "cool", "fans", "average_stars"],
    "review": ["review_id", "user_id", "business_id", "stars", "date", "useful", "funny", "cool"],
}
INTEIROS = {
    "business": ["review_count", "is_open", "price_range", "checkin_count"],
    "users": ["review_count", "useful", "funny", "cool", "fans"],
    "review": ["stars", "useful", "funny", "cool"],
}
ORDEM = ["business", "business_category", "users", "review"]

DDL = """
DROP TABLE IF EXISTS review, business_category, users, business CASCADE;
CREATE TABLE business (
  business_id TEXT PRIMARY KEY, name TEXT, address TEXT, postal_code TEXT,
  latitude DOUBLE PRECISION, longitude DOUBLE PRECISION,
  stars NUMERIC(2,1), review_count INTEGER, is_open INTEGER, price_range INTEGER, checkin_count INTEGER);
CREATE TABLE business_category (business_id TEXT, category TEXT);
CREATE TABLE users (
  user_id TEXT PRIMARY KEY, name TEXT, review_count INTEGER, yelping_since TIMESTAMP,
  useful INTEGER, funny INTEGER, cool INTEGER, fans INTEGER, average_stars NUMERIC(3,2));
CREATE TABLE review (
  review_id TEXT PRIMARY KEY, user_id TEXT, business_id TEXT, stars INTEGER, date TIMESTAMP,
  useful INTEGER, funny INTEGER, cool INTEGER);
"""

POS = """
CREATE INDEX ix_review_business ON review(business_id);
CREATE INDEX ix_review_user ON review(user_id);
CREATE INDEX ix_cat_category ON business_category(category);
CREATE INDEX ix_cat_business ON business_category(business_id);
CREATE INDEX ix_users_name ON users(name);
CREATE INDEX ix_business_name ON business(name);
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'leitor') THEN
    CREATE ROLE leitor LOGIN PASSWORD 'leitor';
  END IF;
END $$;
GRANT CONNECT ON DATABASE yelp TO leitor;
GRANT USAGE ON SCHEMA public TO leitor;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO leitor;
ALTER ROLE leitor SET default_transaction_read_only = on;
ALTER ROLE leitor SET statement_timeout = '10s';
ANALYZE;
"""


def carregar_pg(pasta):
    con = psycopg2.connect(**PG_ADMIN)
    con.autocommit = True
    cur = con.cursor()
    cur.execute(DDL)
    for t in ORDEM:
        t0 = time.time()
        try:
            df = pd.read_csv(f"{pasta}/{t}.csv", usecols=COLS[t])[COLS[t]]
        except ValueError:
            cab = pd.read_csv(f"{pasta}/{t}.csv", nrows=0).columns.tolist()
            raise SystemExit(f"{t}.csv: cabeçalho inesperado.\n  encontrado: {cab}\n  esperado:  {COLS[t]}")
        for c in INTEIROS.get(t, []):
            df[c] = pd.to_numeric(df[c], errors="coerce")
            if c == "checkin_count":
                df[c] = df[c].fillna(0)
            df[c] = df[c].astype("Int64")
        buf = io.StringIO()
        df.to_csv(buf, index=False, header=False)
        buf.seek(0)
        cur.copy_expert(f"COPY {t} ({', '.join(COLS[t])}) FROM STDIN WITH (FORMAT csv)", buf)
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"[pg] {t}: {cur.fetchone()[0]} linhas ({time.time() - t0:.1f}s)")
    cur.execute(POS)
    print("[pg] índices e usuário somente leitura 'leitor' criados")
    con.close()


def lotes(drv, query, linhas, tam=5000):
    for i in range(0, len(linhas), tam):
        with drv.session() as s:
            s.run(query, rows=linhas[i:i + tam]).consume()


def registros(df):
    return df.astype(object).where(df.notna(), None).to_dict("records")


def carregar_neo4j(pasta):
    drv = GraphDatabase.driver(NEO_URI, auth=NEO_AUTH)
    for _ in range(90):  # o Neo4j demora para subir
        try:
            drv.verify_connectivity()
            break
        except Exception:
            time.sleep(2)
    else:
        raise SystemExit("Neo4j não respondeu em 3 minutos. Veja $NEO/logs/neo4j.log")
    t0 = time.time()
    with drv.session() as s:
        while s.run("MATCH (n) WITH n LIMIT 20000 DETACH DELETE n RETURN count(*) AS c").single()["c"]:
            pass
        s.run("CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.user_id IS UNIQUE").consume()
        s.run("CREATE CONSTRAINT business_id IF NOT EXISTS FOR (b:Business) REQUIRE b.business_id IS UNIQUE").consume()
        s.run("CREATE INDEX user_name IF NOT EXISTS FOR (u:User) ON (u.name)").consume()
        s.run("CREATE INDEX business_name IF NOT EXISTS FOR (b:Business) ON (b.name)").consume()

    u = pd.read_csv(f"{pasta}/users.csv", usecols=["user_id", "name"])
    lotes(drv, "UNWIND $rows AS r CREATE (:User {user_id: r.user_id, name: r.name})", registros(u))
    b = pd.read_csv(f"{pasta}/business.csv", usecols=["business_id", "name"])
    lotes(drv, "UNWIND $rows AS r CREATE (:Business {business_id: r.business_id, name: r.name})", registros(b))
    print(f"[neo4j] nós criados ({time.time() - t0:.1f}s)")

    # friends.csv: as duas primeiras colunas são os ids dos usuários
    f = pd.read_csv(f"{pasta}/friends.csv")
    a, c = f.columns[0], f.columns[1]
    f = f[[a, c]].dropna()
    f.columns = ["a", "b"]
    f = f[f["a"] != f["b"]]
    menor = f[["a", "b"]].min(axis=1)
    maior = f[["a", "b"]].max(axis=1)
    f = pd.DataFrame({"a": menor, "b": maior}).drop_duplicates()  # uma aresta por par
    lotes(drv, "UNWIND $rows AS r MATCH (x:User {user_id: r.a}), (y:User {user_id: r.b}) CREATE (x)-[:FRIEND]->(y)",
          registros(f))
    print(f"[neo4j] FRIEND: {len(f)} ({time.time() - t0:.1f}s)")

    r = pd.read_csv(f"{pasta}/reviewed.csv")
    cu = next(c for c in r.columns if "user" in c.lower())
    cb = next(c for c in r.columns if "business" in c.lower())
    r = r[[cu, cb]].dropna().drop_duplicates()
    r.columns = ["u", "b"]
    lotes(drv, "UNWIND $rows AS r MATCH (x:User {user_id: r.u}), (y:Business {business_id: r.b}) CREATE (x)-[:REVIEWED]->(y)",
          registros(r))
    print(f"[neo4j] REVIEWED: {len(r)} ({time.time() - t0:.1f}s)")
    with drv.session() as s:
        print("[neo4j] contagens:", s.run("MATCH (n) RETURN labels(n)[0] AS tipo, count(*) AS n").data())
    drv.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--so", choices=["pg", "neo4j"], help="carregar apenas um dos bancos")
    a = ap.parse_args()
    if a.so != "neo4j":
        carregar_pg(a.dir)
    if a.so != "pg":
        carregar_neo4j(a.dir)
    print("pronto.")
