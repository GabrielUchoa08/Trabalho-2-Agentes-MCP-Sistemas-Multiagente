"""Executa as 20 perguntas nos 3 sistemas (60 execuções) e grava UMA LINHA POR EXECUÇÃO em resultados/resultados.csv.
Grava a cada execução e retoma de onde parou (a sessão do Colab pode cair).

  python -m avaliacao.avaliador                       # tudo
  python -m avaliacao.avaliador --limite 3            # teste rápido: 3 perguntas x 3 sistemas
  python -m avaliacao.avaliador --sistemas unico      # só um sistema
"""
import argparse
import csv
import json
import os

from agentes import sistemas as todos_sistemas
from agentes.comum import carregar_ferramentas
from avaliacao import ambiente
from avaliacao.checagem import checar

COLUNAS = ["id", "sistema", "categoria", "banco", "comportamento", "ferramenta_esperada", "pergunta", "resposta",
           "trajetoria_ok", "valores_ok", "auto_ok", "ferramentas_chamadas", "chamadas_modelo", "chamadas_ferramenta",
           "tokens_in", "tokens_out", "segundos", "cpu_medio", "cpu_max", "ram_mb_medio", "ram_mb_max",
           "gpu_util_medio", "gpu_util_max", "gpu_mem_mb_max", "erro", "extras", "trajetoria_json",
           "juiz_veredito", "juiz_justificativa", "juiz_consultas"]


def ja_feitos(caminho):
    if not os.path.exists(caminho):
        return set()
    with open(caminho, encoding="utf-8", newline="") as f:
        return {(r["id"], r["sistema"]) for r in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perguntas", default="avaliacao/perguntas.json")
    ap.add_argument("--saida", default="resultados/resultados.csv")
    ap.add_argument("--sistemas", nargs="*", default=["unico", "roteamento", "verificador"])
    ap.add_argument("--limite", type=int)
    a = ap.parse_args()

    os.makedirs(os.path.dirname(a.saida), exist_ok=True)
    with open(a.perguntas, encoding="utf-8") as f:
        perguntas = json.load(f)[: a.limite]
    ferramentas = carregar_ferramentas()
    classes = todos_sistemas()
    sis = {n: classes[n](ferramentas) for n in a.sistemas}
    with open("resultados/ambiente.json", "w", encoding="utf-8") as f:
        json.dump(ambiente.coletar(), f, ensure_ascii=False, indent=2)

    print("aquecendo o modelo (carga na GPU)...")
    next(iter(sis.values())).llm.invoke("Responda apenas: ok")

    feitos = ja_feitos(a.saida)
    novo = not os.path.exists(a.saida)
    with open(a.saida, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUNAS)
        if novo:
            w.writeheader()
        for q in perguntas:
            for nome, s in sis.items():
                if (q["id"], nome) in feitos:
                    continue
                r = s.answer(q["pergunta"])
                chk = checar(q, r["resposta"], r["ferramentas_chamadas"])
                linha = {
                    "id": q["id"], "sistema": nome, "categoria": q["categoria"], "banco": q["banco"],
                    "comportamento": q["comportamento"], "ferramenta_esperada": q["ferramenta_esperada"],
                    "pergunta": q["pergunta"], "resposta": r["resposta"], **chk,
                    "ferramentas_chamadas": "|".join(r["ferramentas_chamadas"]),
                    "chamadas_modelo": r["chamadas_modelo"], "chamadas_ferramenta": r["chamadas_ferramenta"],
                    "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"], "segundos": r["segundos"],
                    "cpu_medio": r["cpu_medio"], "cpu_max": r["cpu_max"], "ram_mb_medio": r["ram_mb_medio"],
                    "ram_mb_max": r["ram_mb_max"], "gpu_util_medio": r["gpu_util_medio"],
                    "gpu_util_max": r["gpu_util_max"], "gpu_mem_mb_max": r["gpu_mem_mb_max"],
                    "erro": r["erro"] or "", "extras": json.dumps(r["extras"], ensure_ascii=False),
                    "trajetoria_json": json.dumps(r["trajetoria"], ensure_ascii=False, default=str),
                }
                w.writerow(linha)
                fh.flush()
                print(f"{q['id']} {nome:<11} {r['segundos']:>6.1f}s  traj={chk['trajetoria_ok']!s:<5} "
                      f"auto={chk['auto_ok']!s:<5} tok={r['tokens_in']}/{r['tokens_out']}  {r['resposta'][:70]!r}")


if __name__ == "__main__":
    main()
