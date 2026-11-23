def sistemas():
    """Importação tardia: só carrega as dependências pesadas quando necessário."""
    from .roteamento import Roteamento
    from .unico import AgenteUnico
    from .verificador import Verificador
    return {"unico": AgenteUnico, "roteamento": Roteamento, "verificador": Verificador}
