# -*- coding: utf-8 -*-
"""
iniciar.pyw - abre o Comparador de Contratos como aplicativo.

- Dados do usuario em %LOCALAPPDATA%\\Comparador de Contratos (sobrevivem a reinstalacoes).
- Servidor local (somente 127.0.0.1) em segundo plano, sem janela de console.
- Abre numa janela propria do Microsoft Edge (modo aplicativo); sem Edge, no navegador padrao.
- Se ja estiver aberto, so abre outra janela. Encerra sozinho ~2 h apos fechar a janela
  (suspensao/hibernacao do notebook nao conta como tempo ocioso).
"""

import os
import sys
import json
import time
import socket
import threading
import traceback
import subprocess
import urllib.request
from datetime import datetime

NOME_APP = "Comparador de Contratos"
ASSINATURA = "comparador-contratos"
PORTAS = list(range(8765, 8785))
MINUTOS_OCIOSO = 120

PASTA_APP = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                    "Comparador de Contratos")
PASTA_DADOS = os.path.join(BASE, "dados")
ARQUIVO_ESTADO = os.path.join(BASE, "instancia.json")
PASTA_LOGS = os.path.join(BASE, "logs")


def preparar_ambiente():
    for pasta in (BASE, PASTA_DADOS, PASTA_LOGS, os.path.join(BASE, "ia_config")):
        os.makedirs(pasta, exist_ok=True)
    # pythonw nao tem console: tudo que for impresso vai para o log
    saida = open(os.path.join(PASTA_LOGS, "app_%s.log" % datetime.now().strftime("%Y%m")),
                 "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = saida
    print("\n==== %s  iniciando (%s)" % (datetime.now().isoformat(timespec="seconds"), PASTA_APP))
    os.environ["CONTRATOS_CONFIG_WEB"] = os.path.join(BASE, "config_web.json")
    os.environ["CONTRATOS_PASTA_DADOS"] = PASTA_DADOS
    os.environ["CONTRATOS_IA_CONFIG"] = os.path.join(BASE, "ia_config", "config.json")
    for p in (PASTA_APP, os.path.join(PASTA_APP, "web")):
        if p not in sys.path:
            sys.path.insert(0, p)


def mensagem(texto, erro=False):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, texto, NOME_APP, 0x10 if erro else 0x40)
    except Exception:  # noqa: BLE001
        print(texto)


def responde(porta):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % porta, timeout=2) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == ASSINATURA
    except Exception:  # noqa: BLE001
        return False


def instancia_existente():
    try:
        with open(ARQUIVO_ESTADO, encoding="utf-8") as f:
            porta = int(json.load(f).get("porta"))
    except Exception:  # noqa: BLE001
        return None
    return porta if responde(porta) else None


def porta_livre():
    for porta in PORTAS:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", porta))
            return porta
        except OSError:
            continue
        finally:
            s.close()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


def caminho_edge():
    candidatos = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        raiz = os.environ.get(var)
        if raiz:
            candidatos.append(os.path.join(raiz, "Microsoft", "Edge", "Application", "msedge.exe"))
    return next((c for c in candidatos if os.path.isfile(c)), None)


def abrir_janela(url):
    edge = caminho_edge()
    if edge:
        try:
            subprocess.Popen([edge, "--app=%s" % url, "--window-size=1440,900"],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            return
        except OSError:
            pass
    import webbrowser
    webbrowser.open(url)


def trava_unica():
    """Mutex do Windows: evita dois servidores se o atalho for clicado duas vezes."""
    try:
        import ctypes
        h = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ComparadorContratos")
        ja_existe = ctypes.windll.kernel32.GetLastError() == 183
        return h, ja_existe
    except Exception:  # noqa: BLE001
        return None, False


def principal():
    preparar_ambiente()

    porta = instancia_existente()
    if porta:
        abrir_janela("http://127.0.0.1:%d/" % porta)
        return

    _mutex, ja_existe = trava_unica()
    if ja_existe:
        # outro clique esta subindo o servidor agora: espera ele responder
        for _ in range(60):
            time.sleep(0.5)
            porta = instancia_existente()
            if porta:
                abrir_janela("http://127.0.0.1:%d/" % porta)
                return
        mensagem("O Comparador já está iniciando. Tente de novo em alguns segundos.")
        return

    import app as aplicacao  # noqa: E402 (depende das variaveis de ambiente acima)
    aplicacao.iniciar_servicos()

    porta = porta_livre()
    from waitress import create_server
    servidor = create_server(aplicacao.app, host="127.0.0.1", port=porta, threads=8,
                             ident=None, clear_untrusted_proxy_headers=True)
    threading.Thread(target=servidor.run, daemon=True).start()

    with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump({"porta": porta, "pid": os.getpid(),
                   "iniciado_em": datetime.now().isoformat(timespec="seconds")}, f)

    for _ in range(40):
        if responde(porta):
            break
        time.sleep(0.25)
    print("servidor em http://127.0.0.1:%d/" % porta)
    aplicacao.ULTIMO_SINAL["em"] = datetime.now()
    abrir_janela("http://127.0.0.1:%d/" % porta)

    # encerra quando a janela fica fechada por um tempo (sem comparacoes em andamento)
    ultimo_giro = time.time()
    while True:
        time.sleep(30)
        agora = time.time()
        if agora - ultimo_giro > 120:
            # o notebook dormiu/hibernou: o tempo parado nao e uso ocioso;
            # da tempo da janela voltar a mandar sinal de vida
            print("retomado apos %d s de suspensao" % (agora - ultimo_giro))
            aplicacao.ULTIMO_SINAL["em"] = datetime.now()
        ultimo_giro = agora
        if aplicacao.ocioso_ha_segundos() > MINUTOS_OCIOSO * 60:
            print("sem uso ha %d min: encerrando" % MINUTOS_OCIOSO)
            break
    try:
        os.remove(ARQUIVO_ESTADO)
    except OSError:
        pass
    servidor.close()
    os._exit(0)


if __name__ == "__main__":
    try:
        principal()
    except Exception:  # noqa: BLE001
        detalhe = traceback.format_exc()
        print(detalhe)
        mensagem("Não foi possível abrir o Comparador de Contratos.\n\n%s\n\nDetalhes em:\n%s"
                 % (detalhe.strip().splitlines()[-1], PASTA_LOGS), erro=True)
        os._exit(1)
