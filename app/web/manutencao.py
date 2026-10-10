# -*- coding: utf-8 -*-
"""
manutencao.py
Retencao de uploads e saidas (retencao_dias do config_web.json).

Nada e apagado: itens mais antigos que o prazo sao MOVIDOS para
<dados>/_removidos/<AAAAMMDD_HHMMSS>/..., com log em <dados>/logs/retencao_<data>.log.
Execucoes em revisao (ou aprovadas nos ultimos 30 dias) sao mantidas.

Uso pela linha de comando (ex.: Agendador de Tarefas do Windows):
    python web/manutencao.py --simular
    python web/manutencao.py --executar
"""

import os
import sys
import json
import shutil
import logging
from datetime import datetime, timedelta

STATUS_PROTEGIDOS = ("em_revisao",)
IGNORAR = ("_removidos", "_exemplos_neutros", "_biblioteca", "_lote", "_versoes")


def _tamanho(caminho):
    if os.path.isfile(caminho):
        return os.path.getsize(caminho)
    total = 0
    for raiz, _, arquivos in os.walk(caminho):
        for nome in arquivos:
            try:
                total += os.path.getsize(os.path.join(raiz, nome))
            except OSError:
                pass
    return total


def _data_execucao(execucao):
    try:
        return datetime.fromisoformat(execucao.get("criado_em_iso", ""))
    except ValueError:
        return None


def simular(pasta_dados, dias, execucoes, agora=None):
    """Lista o que seria movido. Retorna {"itens": [...], "mantidos": [...], ...}."""
    agora = agora or datetime.now()
    resultado = {"dias": dias, "limite": None, "itens": [], "mantidos": [], "bytes": 0}
    if not dias or dias <= 0:
        return resultado
    limite = agora - timedelta(days=dias)
    resultado["limite"] = limite.strftime("%d/%m/%Y %H:%M")

    por_pasta = {}
    for execucao in execucoes.values():
        for chave in ("pasta_upload", "pasta_resultado"):
            caminho = execucao.get(chave)
            if caminho:
                por_pasta[os.path.normcase(os.path.basename(os.path.normpath(caminho)))] = execucao

    for sub in ("uploads", "saida"):
        base = os.path.join(pasta_dados, sub)
        if not os.path.isdir(base):
            continue
        for dep in sorted(os.listdir(base)):
            pasta_dep = os.path.join(base, dep)
            if dep in IGNORAR or not os.path.isdir(pasta_dep):
                continue
            for nome in sorted(os.listdir(pasta_dep)):
                caminho = os.path.join(pasta_dep, nome)
                if nome in IGNORAR or nome.startswith("_") or not os.path.isdir(caminho):
                    continue
                execucao = por_pasta.get(os.path.normcase(nome))
                data = _data_execucao(execucao) if execucao else None
                if data is None:
                    data = datetime.fromtimestamp(os.path.getmtime(caminho))
                if data >= limite:
                    continue
                relativo = os.path.relpath(caminho, pasta_dados)
                item = {"caminho": relativo.replace(os.sep, "/"), "tipo": sub,
                        "departamento": dep, "data": data.strftime("%d/%m/%Y"),
                        "execucao": execucao.get("id") if execucao else None,
                        "fornecedor": execucao.get("fornecedor") if execucao else None}
                status = (execucao or {}).get("status_revisao")
                if status in STATUS_PROTEGIDOS:
                    item["motivo"] = "em revisão"
                    resultado["mantidos"].append(item)
                    continue
                item["bytes"] = _tamanho(caminho)
                resultado["bytes"] += item["bytes"]
                resultado["itens"].append(item)
    return resultado


def _logger(pasta_dados):
    log = logging.getLogger("retencao")
    log.setLevel(logging.INFO)
    log.handlers = []
    pasta_logs = os.path.join(pasta_dados, "logs")
    os.makedirs(pasta_logs, exist_ok=True)
    fh = logging.FileHandler(os.path.join(pasta_logs, "retencao_%s.log"
                                          % datetime.now().strftime("%Y%m%d")),
                             encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s",
                                      "%Y-%m-%d %H:%M:%S"))
    log.addHandler(fh)
    return log


def executar(pasta_dados, dias, execucoes, agora=None):
    """Move os itens simulados para _removidos/<carimbo>/ e devolve o relatorio."""
    agora = agora or datetime.now()
    plano = simular(pasta_dados, dias, execucoes, agora)
    if not plano["itens"]:
        return dict(plano, movidos=0, destino=None)
    log = _logger(pasta_dados)
    carimbo = agora.strftime("%Y%m%d_%H%M%S")
    destino_base = os.path.join(pasta_dados, "_removidos", "retencao_" + carimbo)
    movidos = 0
    raiz = os.path.abspath(pasta_dados)
    for item in plano["itens"]:
        origem = os.path.abspath(os.path.join(pasta_dados, item["caminho"]))
        if not origem.startswith(raiz + os.sep) or not os.path.exists(origem):
            continue
        destino = os.path.join(destino_base, item["caminho"])
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        shutil.move(origem, destino)
        movidos += 1
        log.info("movido (retencao %d dias): %s -> %s", dias, item["caminho"],
                 os.path.relpath(destino, pasta_dados))
        execucao = execucoes.get(item.get("execucao") or "")
        if execucao:
            execucao["arquivado"] = True
            execucao["arquivado_em"] = agora.strftime("%d/%m/%Y %H:%M")
            execucao.setdefault("arquivado_em_pasta", os.path.relpath(
                destino_base, pasta_dados).replace(os.sep, "/"))
    log.info("retencao concluida: %d item(ns) movido(s), %d mantido(s) em revisao",
             movidos, len(plano["mantidos"]))
    return dict(plano, movidos=movidos,
                destino=os.path.relpath(destino_base, pasta_dados).replace(os.sep, "/"))


def main(argv=None):
    argv = argv or sys.argv[1:]
    pasta_web = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, pasta_web)
    import app as aplicacao                   # usa a mesma configuracao do servidor
    aplicacao.EXECUCOES.update(aplicacao.carregar_historico())
    dias = int(aplicacao.CFG_WEB.get("retencao_dias") or 0)
    if dias <= 0:
        print("retencao_dias = 0: nada a fazer.")
        return 0
    if "--executar" in argv:
        rel = executar(aplicacao.PASTA_DADOS, dias, aplicacao.EXECUCOES)
        aplicacao.gravar_historico()
        print("%d item(ns) movido(s) para %s" % (rel["movidos"], rel["destino"]))
    else:
        rel = simular(aplicacao.PASTA_DADOS, dias, aplicacao.EXECUCOES)
        for item in rel["itens"]:
            print("moveria: %s (%s, %.1f KB)" % (item["caminho"], item["data"],
                                                 item["bytes"] / 1024))
        for item in rel["mantidos"]:
            print("mantido: %s (%s)" % (item["caminho"], item["motivo"]))
        print("Total: %d item(ns), %.1f MB. Use --executar para mover." % (
            len(rel["itens"]), rel["bytes"] / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
