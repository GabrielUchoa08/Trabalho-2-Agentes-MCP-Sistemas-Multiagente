"""Checagem automática (barata, heurística): trajetória + valores obrigatórios/proibidos na resposta.
O veredito principal de acerto é do juiz de fronteira; isto serve de apoio e de terceira opinião na concordância."""
import re
import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return s


def numeros(texto: str) -> list[float]:
    """Extrai números do texto aceitando 1,234.5 e 1.234,5 (devolve todas as leituras possíveis)."""
    vals = []
    for tok in re.findall(r"\d[\d.,]*\d|\d", texto or ""):
        for conv in (lambda t: t.replace(",", ""), lambda t: t.replace(".", "").replace(",", ".")):
            try:
                vals.append(float(conv(tok)))
            except ValueError:
                pass
    return vals


def tem_numero(texto: str, alvo: float, tol: float) -> bool:
    return any(abs(v - alvo) <= tol for v in numeros(texto))


def trajetoria_ok(q: dict, chamadas: list[str]) -> bool:
    esperadas = q.get("ferramentas_esperadas", [])
    if not esperadas:  # fora do escopo: nenhuma ferramenta deve ser chamada
        return len(chamadas) == 0
    if q.get("modo_trajetoria", "qualquer") == "todas":
        return all(e in chamadas for e in esperadas)
    return any(e in chamadas for e in esperadas)


def valores_ok(q: dict, resposta: str) -> bool:
    r = _norm(resposta)
    tol_def = q.get("tol", 0.05)
    for n in q.get("numericos", []):
        if not tem_numero(resposta, float(n), 0 if float(n).is_integer() else tol_def):
            return False
    qq = q.get("numericos_qualquer", [])
    if qq and not any(tem_numero(resposta, float(n), 0 if float(n).is_integer() else tol_def) for n in qq):
        return False
    for o in q.get("obrigatorios", []):
        if _norm(str(o)) not in r:
            return False
    oq = q.get("obrigatorios_qualquer", [])
    if oq and not any(_norm(str(o)) in r for o in oq):
        return False
    for p in q.get("proibidos", []):
        if _norm(str(p)) in r:
            return False
    return True


def checar(q: dict, resposta: str, chamadas: list[str]) -> dict:
    t, v = trajetoria_ok(q, chamadas), valores_ok(q, resposta)
    return {"trajetoria_ok": t, "valores_ok": v, "auto_ok": t and v}
