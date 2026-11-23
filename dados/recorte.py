"""Recorte do Yelp Open Dataset: Santa Barbara, CA inteira. Roda UMA vez no seu computador.

  python dados/recorte.py "Yelp-JSON/Yelp JSON/yelp_dataset" dados/recorte

Lê os JSON linha a linha (sem carregar tudo na memória) e grava 6 CSV pequenos:
  business.csv, business_category.csv, users.csv, review.csv, friends.csv, reviewed.csv
Depois envie a pasta de saída para o Google Drive (Meu Drive/yelp_recorte).

Regras (PLANO.md): todos os tipos de negócio; reviews desses negócios, SEM o texto; usuários que as escreveram;
amizades somente entre usuários do recorte; uma review cujo autor não existe em user.json é descartada.
"""
import csv
import glob
import json
import os
import sys

CIDADE, ESTADO = "Santa Barbara", "CA"


def achar(pasta, nome):
    achados = glob.glob(os.path.join(pasta, f"*{nome}*.json"))
    if not achados:
        sys.exit(f"Não achei um arquivo *{nome}*.json em {pasta}")
    return achados[0]


def linhas(caminho):
    with open(caminho, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                yield json.loads(ln)


def preco(attrs):
    """RestaurantsPriceRange2 vem como texto ('2'), 'None' ou ausente."""
    v = (attrs or {}).get("RestaurantsPriceRange2")
    try:
        return int(str(v).strip("'\""))
    except (TypeError, ValueError):
        return ""


def gravar(caminho, cabecalho, registros):
    n = 0
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cabecalho)
        for r in registros:
            w.writerow(r)
            n += 1
    print(f"{os.path.basename(caminho):<24} {n:>8} linhas")


def main(pasta, saida):
    os.makedirs(saida, exist_ok=True)

    # 1) negócios da cidade
    neg, cats = {}, []
    for b in linhas(achar(pasta, "business")):
        if b.get("city") == CIDADE and b.get("state") == ESTADO:
            neg[b["business_id"]] = [b["business_id"], b["name"], b.get("address"), b.get("postal_code"),
                                     b.get("latitude"), b.get("longitude"), b.get("stars"), b.get("review_count"),
                                     b.get("is_open"), preco(b.get("attributes")), 0]
            for c in (b.get("categories") or "").split(","):
                if c.strip():
                    cats.append((b["business_id"], c.strip()))
    print(f"negócios em {CIDADE}: {len(neg)}")

    # checkins: uma linha por negócio, datas num texto só -> contagem
    for c in linhas(achar(pasta, "checkin")):
        if c["business_id"] in neg:
            neg[c["business_id"]][10] = len((c.get("date") or "").split(", "))

    # 2) reviews desses negócios (sem o texto)
    revs, autores = [], set()
    for r in linhas(achar(pasta, "review")):
        if r["business_id"] in neg:
            revs.append(r)
            autores.add(r["user_id"])

    # 3) usuários que as escreveram (os que não existem em user.json saem junto com suas reviews)
    usuarios, amigos_brutos = [], {}
    for u in linhas(achar(pasta, "user")):
        if u["user_id"] in autores:
            usuarios.append([u["user_id"], u["name"], u["review_count"], u["yelping_since"], u["useful"],
                             u["funny"], u["cool"], u["fans"], u["average_stars"]])
            amigos_brutos[u["user_id"]] = u.get("friends") or ""
    ids = set(amigos_brutos)
    revs = [r for r in revs if r["user_id"] in ids]
    descartadas = len(autores) - len(ids)
    print(f"usuários: {len(ids)} (autores sem registro em user.json: {descartadas})")

    # 4) amizades só entre usuários do recorte, um registro por par não ordenado
    pares = set()
    for uid, txt in amigos_brutos.items():
        if txt.strip() in ("", "None"):
            continue
        for f in txt.split(","):
            f = f.strip()
            if f in ids and f != uid:
                pares.add((min(uid, f), max(uid, f)))

    cab_b = ["business_id", "name", "address", "postal_code", "latitude", "longitude", "stars", "review_count",
             "is_open", "price_range", "checkin_count"]
    gravar(os.path.join(saida, "business.csv"), cab_b, neg.values())
    gravar(os.path.join(saida, "business_category.csv"), ["business_id", "category"], cats)
    gravar(os.path.join(saida, "users.csv"), ["user_id", "name", "review_count", "yelping_since", "useful", "funny",
                                              "cool", "fans", "average_stars"], usuarios)
    gravar(os.path.join(saida, "review.csv"), ["review_id", "user_id", "business_id", "stars", "date", "useful",
                                               "funny", "cool"],
           ([r["review_id"], r["user_id"], r["business_id"], r["stars"], r["date"], r["useful"], r["funny"], r["cool"]]
            for r in revs))
    gravar(os.path.join(saida, "friends.csv"), ["user_id", "friend_id"], sorted(pares))
    gravar(os.path.join(saida, "reviewed.csv"), ["user_id", "business_id"],
           sorted({(r["user_id"], r["business_id"]) for r in revs}))
    print("\nEsperado para Santa Barbara (PLANO.md): business 3834 | category 18194 | users 130392 | "
          "review 269703 | friends 377374 | reviewed 262936")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
