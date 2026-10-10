# -*- coding: utf-8 -*-
"""
app.py - Comparador de Contratos (versao web)
Roda localmente em http://127.0.0.1:5000 e migra para VPS (Gunicorn + Nginx) sem
alteracao de codigo: `gunicorn -w 1 --threads 8 wsgi:app` (ver README.md).
"""

import os
import io
import re
import sys
import json
import uuid
import copy
import queue
import shutil
import logging
import zipfile
import platform
import threading
from datetime import datetime

from flask import (Flask, render_template, request, jsonify, send_file,
                   redirect, url_for, abort, Response)
from werkzeug.utils import secure_filename

PASTA_WEB = os.path.dirname(os.path.abspath(__file__))
PASTA_RAIZ = os.path.dirname(PASTA_WEB)
for _p in (PASTA_RAIZ, PASTA_WEB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import comparar_contratos as motor  # noqa: E402
import identidade as identidade_mod  # noqa: E402
import configuracao  # noqa: E402
import manutencao  # noqa: E402
import recursos  # noqa: E402
import redline  # noqa: E402
import parecer  # noqa: E402
import analise_ia  # noqa: E402
import ia_filtro  # noqa: E402

try:
    import docusign_integracao as docusign
    DOCUSIGN_DISPONIVEL = True
    _MOTIVO_DOCUSIGN_INDISPONIVEL = ""
except Exception as _erro_docusign:  # noqa: BLE001
    docusign = None
    DOCUSIGN_DISPONIVEL = False
    _MOTIVO_DOCUSIGN_INDISPONIVEL = str(_erro_docusign)

# Recursos de IA desligados por padrao. Para ativar,
# defina a variavel de ambiente CONTRATOS_IA_DISPONIVEL=1 e configure ia_config/config.json.
IA_DISPONIVEL = os.environ.get("CONTRATOS_IA_DISPONIVEL") == "1"
if not IA_DISPONIVEL:
    analise_ia.habilitada = lambda *_a, **_k: False

# variaveis de ambiente permitem isolar testes e apontar outra instalacao (VPS)
CAMINHO_CONFIG_WEB = (os.environ.get("CONTRATOS_CONFIG_WEB")
                      or os.path.join(PASTA_WEB, "config_web.json"))
# perfis de identidade prontos (marcas ficam nesta pasta, nunca no codigo)
PASTA_PERFIS = (os.environ.get("CONTRATOS_PASTA_PERFIS")
                or os.path.join(PASTA_RAIZ, "perfis"))

CONFIG_WEB_PADRAO = {
    "pasta_dados": os.path.join(PASTA_WEB, "dados"),
    "departamentos": [],         # cadastrados pelo usuario em Ajustes > Departamentos
    "host": "127.0.0.1",
    "porta": 5000,
    "abrir_navegador": True,
    "tamanho_maximo_mb": 40,
    "extensoes_aceitas": [".docx", ".pdf", ".txt"],
    "retencao_dias": 0,
    "docusign": {},              # preenchido por departamento — nunca compartilhado
    "identidade": dict(identidade_mod.IDENTIDADE_PADRAO),
    "perfil_identidade": "padrao",
}
DOCUSIGN_PADRAO_DEPTO = configuracao.DOCUSIGN_PADRAO_DEPTO

NOMES_TIPO = {"PREENCHIDO": "Preenchido", "ALTERADO": "Alterado", "INCLUIDO": "Inclu\u00eddo",
              "REMOVIDO": "Removido", "MOVIDO": "Movido", "PENDENTE": "Pendente",
              "FORMATACAO": "Formata\u00e7\u00e3o"}

ARQUIVOS_SAIDA = {
    "json": ("resultado.json", "application/json"),
    "pdf": ("comparativo.pdf", "application/pdf"),
    "xlsx": ("alteracoes.xlsx",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "html": ("comparativo.html", "text/html"),
    "txt": ("resumo_alteracoes.txt", "text/plain"),
    "redline": (redline.NOME_ARQUIVO,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "parecer_pdf": ("parecer.pdf", "application/pdf"),
    "parecer_docx": ("parecer.docx",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
}
STATUS_REVISAO = ("pendente", "em_revisao", "aprovado", "devolvido")


# ---------------------------------------------------------------- configuracao

def _gravar_config_simples(cfg):
    tmp = CAMINHO_CONFIG_WEB + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CAMINHO_CONFIG_WEB)


def carregar_config_web():
    if not os.path.exists(CAMINHO_CONFIG_WEB):
        cfg = configuracao.completar(copy.deepcopy(CONFIG_WEB_PADRAO))
        _gravar_config_simples(cfg)
        return cfg
    with open(CAMINHO_CONFIG_WEB, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    original = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
    _migrar_ia_antiga(cfg)
    for chave, valor in CONFIG_WEB_PADRAO.items():
        cfg.setdefault(chave, copy.deepcopy(valor))
    configuracao.completar(cfg)

    # cada departamento tem conta DocuSign propria e isolada; nunca compartilhar
    # credenciais entre departamentos, mesmo que um esteja vazio.
    docusign_por_depto = cfg.get("docusign") or {}
    if not isinstance(docusign_por_depto, dict) or (
            docusign_por_depto and "ativo" in docusign_por_depto
            and not any(isinstance(v, dict) for v in docusign_por_depto.values())):
        # formato antigo (um unico bloco global) - nao migra sozinho: cada
        # departamento precisa das PROPRIAS credenciais, entao comeca vazio.
        docusign_por_depto = {}
    for departamento in cfg.get("departamentos", []):
        codigo = departamento.get("codigo")
        if codigo and codigo not in docusign_por_depto:
            docusign_por_depto[codigo] = dict(DOCUSIGN_PADRAO_DEPTO)
        elif codigo:
            for chave, valor in DOCUSIGN_PADRAO_DEPTO.items():
                docusign_por_depto[codigo].setdefault(chave, valor)
    cfg["docusign"] = docusign_por_depto

    if json.dumps(cfg, sort_keys=True, ensure_ascii=False) != original:
        _gravar_config_simples(cfg)            # migracao idempotente
    return cfg


def _migrar_ia_antiga(cfg):
    """Versoes anteriores guardavam liberacao/modelo/chave no config_web.json.
    Agora ficam em ia_config/config.json: move uma vez, sem perder nada."""
    ia = cfg.get("ia") or {}
    permitida = cfg.get("ia_externa_permitida") is True
    chave, modelo = ia.get("chave_api") or "", ia.get("modelo") or ""
    if not (permitida or chave):
        return
    try:
        conexao = analise_ia.carregar_conexao()
        if permitida:
            conexao["ia_externa_permitida"] = True
        if chave and not conexao["anthropic"].get("chave_api"):
            conexao["anthropic"]["chave_api"] = chave
            conexao["anthropic"]["modelo"] = modelo or conexao["anthropic"]["modelo"]
            if not analise_ia.pendencias_conexao(dict(conexao, provedor="anthropic")) and \
                    analise_ia.pendencias_conexao(conexao):
                conexao["provedor"] = "anthropic"
        analise_ia.gravar_conexao(conexao)
    except OSError:
        return


def resolver_pasta_dados(valor):
    """Usa a pasta do config se ela fizer sentido neste sistema operacional;
    senao cai para web/dados (ex.: caminho C:\\... lido numa VPS Linux)."""
    if os.environ.get("CONTRATOS_PASTA_DADOS"):
        return os.environ["CONTRATOS_PASTA_DADOS"]
    if valor and os.path.isabs(valor):
        pai = os.path.dirname(os.path.normpath(valor))
        if os.path.isdir(valor) or os.path.isdir(pai):
            return valor
    return os.path.join(PASTA_WEB, "dados")


CFG_WEB = carregar_config_web()
PASTA_DADOS = resolver_pasta_dados(CFG_WEB.get("pasta_dados"))
PASTA_MODELOS = os.path.join(PASTA_DADOS, "modelos")
PASTA_SAIDA = os.path.join(PASTA_DADOS, "saida")
PASTA_UPLOADS = os.path.join(PASTA_DADOS, "uploads")
PASTA_LOGS = os.path.join(PASTA_DADOS, "logs")
PASTA_BACKUPS = os.path.join(PASTA_DADOS, "config_backups")
PASTA_IDENTIDADE = os.path.join(PASTA_DADOS, "identidade")
PASTA_SEGREDOS = os.path.join(PASTA_DADOS, "segredos")
ARQUIVO_HISTORICO = os.path.join(PASTA_DADOS, "historico.json")


def codigos_departamento(so_ativos=False):
    return [d["codigo"] for d in CFG_WEB["departamentos"]
            if not so_ativos or d.get("ativo", True)]


def departamentos_ativos():
    return [d for d in CFG_WEB["departamentos"]
            if d.get("ativo", True) and not d.get("excluido")]


def departamentos_visiveis():
    """Todos menos os excluidos (arquivados com historico)."""
    return [d for d in CFG_WEB["departamentos"] if not d.get("excluido")]


def departamentos_com_dados():
    """Codigos com historico ou modelos: esses sao arquivados, nunca apagados."""
    codigos = {e.get("departamento") for e in EXECUCOES.values()}
    codigos.update(c for c, modelos in modelos_por_departamento().items() if modelos)
    return codigos


def departamento(codigo):
    for d in CFG_WEB["departamentos"]:
        if d["codigo"] == codigo:
            return d
    return None


def nome_departamento(codigo):
    d = departamento(codigo)
    return d["nome"] if d else codigo


def dep_da_requisicao():
    """Departamento pedido na URL; cai no primeiro ativo ("" se nenhum cadastrado)."""
    ativos = codigos_departamento(so_ativos=True) or codigos_departamento()
    if not ativos:
        return ""
    codigo = request.args.get("dep") or ativos[0]
    return codigo if codigo in codigos_departamento() else ativos[0]


def identidade_atual():
    return identidade_mod.mesclar(CFG_WEB.get("identidade"))


def caminho_logo_configurado():
    """Arquivo de logo (dados/ ou pasta do perfil) ou None = monograma."""
    return identidade_mod.caminho_logo(identidade_atual().get("logo"), PASTA_DADOS,
                                       PASTA_PERFIS)


def perfis_identidade():
    return identidade_mod.listar_perfis(PASTA_PERFIS)


def perfil_ativo():
    return CFG_WEB.get("perfil_identidade") or "padrao"


_CACHE_TOKENS = {}


def tokens_identidade():
    cor = identidade_atual()["cor_primaria"]
    if cor not in _CACHE_TOKENS:
        _CACHE_TOKENS.clear()
        _CACHE_TOKENS[cor] = identidade_mod.gerar_tokens(cor)
    return _CACHE_TOKENS[cor]


def cfg_docusign(codigo):
    """Credenciais DocuSign do departamento. Nunca cai para as de outro."""
    return CFG_WEB.get("docusign", {}).get(codigo, dict(DOCUSIGN_PADRAO_DEPTO))


def preparar_pastas():
    for base in (PASTA_MODELOS, PASTA_SAIDA, PASTA_UPLOADS):
        for codigo in codigos_departamento():
            os.makedirs(os.path.join(base, codigo), exist_ok=True)
    os.makedirs(PASTA_LOGS, exist_ok=True)


def _config_desktop():
    """config.json do motor (versao desktop): chaves de analise legadas."""
    caminho = os.path.join(PASTA_RAIZ, "config.json")
    if not os.path.exists(caminho):
        return {}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def regras_analise(codigo_dep):
    """Regras efetivas do departamento: padrao do motor < config.json (legado) <
    global (Ajustes) < departamento (Ajustes). Tambem diz a origem de cada chave."""
    regras, origem = {}, {}
    salvo = _config_desktop()
    for chave in configuracao.CHAVES_ANALISE:
        regras[chave] = copy.deepcopy(motor.CONFIG_PADRAO.get(chave))
        origem[chave] = "padr\u00e3o"
        if chave in salvo:
            regras[chave] = salvo[chave]
            origem[chave] = "config.json"
    analise = CFG_WEB.get("analise") or {}
    for chave, valor in (analise.get("global") or {}).items():
        regras[chave], origem[chave] = copy.deepcopy(valor), "global"
    for chave, valor in ((analise.get("departamentos") or {}).get(codigo_dep) or {}).items():
        regras[chave], origem[chave] = copy.deepcopy(valor), "departamento"
    return regras, origem


def playbook_do_departamento(codigo_dep):
    return [r for r in CFG_WEB.get("playbook") or []
            if r.get("ativo", True) and (not r.get("departamentos")
                                         or codigo_dep in r["departamentos"])]


def cfg_motor(codigo_dep, pasta_upload):
    """Monta o config do motor para um departamento especifico."""
    base = copy.deepcopy(motor.CONFIG_PADRAO)
    salvo = _config_desktop()
    for chave in ("formato_data_pasta", "formato_hora_pasta", "incluir_hora_na_pasta"):
        if chave in salvo:
            base[chave] = salvo[chave]
    regras, _ = regras_analise(codigo_dep)
    base.update(regras)
    saidas = CFG_WEB.get("saidas") or {}
    base.update({k: saidas[k] for k in configuracao.CHAVES_SAIDAS if k in saidas})
    base.update({
        "pasta_modelo": os.path.join(PASTA_MODELOS, codigo_dep),
        "pasta_devolucoes": pasta_upload,
        "pasta_saida": os.path.join(PASTA_SAIDA, codigo_dep),
        "pasta_logs": PASTA_LOGS,
        "extensoes_aceitas": CFG_WEB["extensoes_aceitas"],
        "modo_teste": False,
        "gravar_saida": True,
        "gerar_json": True,                      # base do HTML, revisao e historico
        "copiar_arquivos_origem": False,
        "fornecedor_forcado": "",
        "identidade": identidade_atual(),
        "caminho_logo": caminho_logo_configurado() or "",
        "playbook": playbook_do_departamento(codigo_dep),
        "biblioteca_aprovada": recursos.mapa_biblioteca(PASTA_DADOS, codigo_dep),
    })
    return base


def aplicar_config(nova):
    """Recarrega a configuracao sem reiniciar o servidor."""
    with _trava_config:
        CFG_WEB.clear()
        CFG_WEB.update(nova)
        app.config["MAX_CONTENT_LENGTH"] = int(CFG_WEB["tamanho_maximo_mb"]) * 1024 * 1024
        _CACHE_TOKENS.clear()
        preparar_pastas()


_trava_config = threading.Lock()


# -------------------------------------------------------------------- historico

_trava = threading.RLock()
EXECUCOES = {}


def carregar_historico():
    if not os.path.exists(ARQUIVO_HISTORICO):
        return {}
    try:
        with open(ARQUIVO_HISTORICO, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def gravar_historico():
    tmp = ARQUIVO_HISTORICO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(EXECUCOES, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ARQUIVO_HISTORICO)


def salvar_execucao(execucao):
    with _trava:
        EXECUCOES[execucao["id"]] = execucao
        gravar_historico()


def resolver_caminho_dados(caminho):
    """Caminhos gravados no historico podem ser de outra maquina (C:\\...\\dados\\...).
    Reaponta para a pasta de dados atual quando o original nao existe."""
    if not caminho:
        return caminho
    if os.path.exists(caminho):
        return caminho
    partes = re.split(r"[\\/]+", caminho)
    for i in range(len(partes) - 1, -1, -1):
        if partes[i].lower() == "dados":
            candidato = os.path.join(PASTA_DADOS, *partes[i + 1:])
            if os.path.exists(candidato):
                return candidato
            break
    return caminho


def pasta_resultado(execucao):
    return resolver_caminho_dados(execucao.get("pasta_resultado") or "")


# ---------------------------------------------------------------------- worker

FILA = queue.Queue()


def _logger_execucao(execucao_id):
    log = logging.getLogger("web.%s" % execucao_id)
    log.setLevel(logging.INFO)
    log.handlers = []
    caminho = os.path.join(PASTA_LOGS, "web_%s.log"
                           % datetime.now().strftime("%Y%m%d"))
    fh = logging.FileHandler(caminho, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s",
                                      "%Y-%m-%d %H:%M:%S"))
    log.addHandler(fh)
    return log


def _processar_execucao(execucao_id):
    execucao = EXECUCOES.get(execucao_id)
    if not execucao:
        return
    execucao["status"] = "processando"
    salvar_execucao(execucao)

    log = _logger_execucao(execucao_id)
    try:
        cfg = cfg_motor(execucao["departamento"], execucao["pasta_upload"])
        cfg["fornecedor_forcado"] = execucao.get("fornecedor_forcado", "")
        modelo = resolver_caminho_dados(execucao["caminho_modelo"])
        meta, resumo, destino = motor.processar(
            resolver_caminho_dados(execucao["caminho_arquivo"]), [modelo], cfg, log)

        execucao.update({
            "status": "concluido",
            "fornecedor": meta["fornecedor"],
            "cnpj": meta["cnpj"],
            "modelo": meta["arquivo_modelo"],
            "paragrafos_identicos": round(meta["similaridade_global"], 1),
            "alterados": resumo["ALTERADO"]["total"],
            "incluidos": resumo["INCLUIDO"]["total"],
            "removidos": resumo["REMOVIDO"]["total"],
            "movidos": resumo["MOVIDO"]["total"],
            "preenchidos": resumo.get("PREENCHIDO", {}).get("total", 0),
            "pendentes": resumo.get("PENDENTE", {}).get("total", 0),
            "formatacao": resumo.get("FORMATACAO", {}).get("total", 0),
            "criticos": resumo["TOTAL"]["criticos"],
            "total": resumo["TOTAL"]["total"],
            "risco_geral": meta.get("risco_geral"),
            "selo_risco": meta.get("selo_risco"),
            "devolucao_incompleta": meta.get("devolucao_incompleta", False),
            "fora_da_politica": meta.get("fora_da_politica", 0),
            "hash_entrada": meta.get("hash_entrada", ""),
            "versao_motor": meta.get("versao_motor", motor.VERSAO),
            "pasta_resultado": destino,
            "concluido_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "concluido_em_iso": datetime.now().isoformat(timespec="seconds"),
        })
        execucao.setdefault("status_revisao", "pendente")
        pos_processamento(execucao, meta.get("resultado"))
    except Exception as erro:  # noqa: BLE001
        log.exception("Falha na execucao %s", execucao_id)
        execucao.update({"status": "erro", "erro": str(erro)[:400]})
    salvar_execucao(execucao)


def pos_processamento(execucao, resultado):
    """Apos cada execucao concluida: resumo por clausula (painel) e rodadas."""
    execucao["clausulas_alteradas"] = recursos.clausulas_alteradas(resultado)
    execucao["aprovadas_antes"] = (resultado or {}).get("meta", {}).get("aprovadas_antes", 0)
    with _trava:
        EXECUCOES[execucao["id"]] = execucao
        recursos.numerar_rodadas(EXECUCOES)


def _worker():
    while True:
        execucao_id = FILA.get()
        try:
            _processar_execucao(execucao_id)
        except Exception:  # noqa: BLE001
            logging.exception("Worker falhou")
        finally:
            FILA.task_done()


# ------------------------------------------------------------------------- app

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(CFG_WEB["tamanho_maximo_mb"]) * 1024 * 1024


@app.context_processor
def _contexto_identidade():
    """Nome, logo e tokens de cor disponiveis em todos os templates."""
    ident = identidade_atual()
    ia_ativa = analise_ia.habilitada(CFG_WEB, conexao_ia())
    return {"ident": ident,
            "tokens_css": identidade_mod.css_variaveis(tokens_identidade()),
            "versao_motor": motor.VERSAO,
            "versao_logo": re.sub(r"[^\w]", "", ident["cor_primaria"] + ident["nome_produto"]
                                  + (ident.get("logo") or ""))[:60],
            "nomes_tipo": NOMES_TIPO,
            "ia_ativa": ia_ativa,
            "ia_recursos": {r: ia_ativa and bool((CFG_WEB.get("ia") or {}).get(r, True))
                            for r in configuracao.RECURSOS_IA},
            "departamentos_menu": departamentos_ativos()}


# ------------------------------------------------ primeiro uso e sinal de vida

# paginas que exigem ao menos um departamento cadastrado
_PAGINAS_COM_DEPARTAMENTO = ("pagina_comparar", "pagina_historico", "pagina_painel",
                             "pagina_modelos")
ULTIMO_SINAL = {"em": datetime.now()}


@app.before_request
def _exigir_departamento():
    """Sem departamento cadastrado, as paginas de trabalho levam ao cadastro."""
    ULTIMO_SINAL["em"] = datetime.now()
    if request.endpoint in _PAGINAS_COM_DEPARTAMENTO and not departamentos_ativos():
        return redirect(url_for("pagina_ajustes", aba="departamentos", primeiro_uso=1))
    return None


@app.route("/api/ping", methods=["GET", "POST"])
def api_ping():
    """Sinal de vida da janela (usado para encerrar o app quando ela e fechada)."""
    ULTIMO_SINAL["em"] = datetime.now()
    return jsonify({"ok": True, "app": "comparador-contratos"})


def ocioso_ha_segundos():
    """Segundos sem nenhuma requisicao; 0 se houver comparacao na fila/processando."""
    if FILA.qsize() or any(e.get("status") in ("na_fila", "processando")
                           for e in EXECUCOES.values()):
        return 0
    return (datetime.now() - ULTIMO_SINAL["em"]).total_seconds()


@app.route("/identidade/logo")
def logo_identidade():
    caminho = caminho_logo_configurado()
    cabecalhos = {"Cache-Control": "max-age=60",
                  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                  "X-Content-Type-Options": "nosniff"}
    if caminho:
        resposta = send_file(caminho, max_age=60)
        resposta.headers.update(cabecalhos)
        return resposta
    ident = identidade_atual()
    svg = identidade_mod.logo_svg(ident["nome_produto"], ident["cor_primaria"])
    return Response(svg, mimetype="image/svg+xml", headers=cabecalhos)


@app.route("/identidade/perfil/<perfil_id>/logo")
def logo_perfil(perfil_id):
    """Logo do perfil (como esta personalizado, ou o padrao da pasta)."""
    perfil = next((p for p in perfis_identidade() if p["id"] == perfil_id), None)
    if not perfil:
        abort(404)
    ident = identidade_mod.mesclar((CFG_WEB.get("identidade_perfis") or {}).get(perfil_id)
                                   or perfil["identidade"])
    cabecalhos = {"Cache-Control": "max-age=60",
                  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                  "X-Content-Type-Options": "nosniff"}
    caminho = identidade_mod.caminho_logo(ident.get("logo"), PASTA_DADOS, PASTA_PERFIS)
    if caminho:
        resposta = send_file(caminho, max_age=60)
        resposta.headers.update(cabecalhos)
        return resposta
    svg = identidade_mod.logo_svg(ident["nome_produto"], ident["cor_primaria"])
    return Response(svg, mimetype="image/svg+xml", headers=cabecalhos)


def caminho_meta(codigo):
    return os.path.join(PASTA_MODELOS, codigo, "_meta.json")


def ler_meta(codigo):
    caminho = caminho_meta(codigo)
    if not os.path.exists(caminho):
        return {}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def gravar_meta(codigo, dados):
    caminho = caminho_meta(codigo)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)


def caminho_modelo_seguro(codigo, arquivo):
    """Impede que o nome do arquivo escape da pasta do departamento."""
    if codigo not in codigos_departamento():
        abort(404)
    nome = secure_filename(arquivo or "")
    if not nome:
        abort(404)
    pasta = os.path.abspath(os.path.join(PASTA_MODELOS, codigo))
    caminho = os.path.abspath(os.path.join(pasta, nome))
    if not caminho.startswith(pasta + os.sep) or not os.path.exists(caminho):
        abort(404)
    return caminho, nome


def modelos_do_departamento(codigo):
    pasta = os.path.join(PASTA_MODELOS, codigo)
    if not os.path.isdir(pasta):
        return []
    extensoes = [e.lower() for e in CFG_WEB["extensoes_aceitas"]]
    meta = ler_meta(codigo)
    itens = []
    for nome in sorted(os.listdir(pasta)):
        caminho = os.path.join(pasta, nome)
        if (nome.startswith("~$") or not os.path.isfile(caminho)
                or os.path.splitext(nome)[1].lower() not in extensoes):
            continue
        registro = meta.get(nome, {})
        data_iso = registro.get("data") or datetime.fromtimestamp(
            os.path.getmtime(caminho)).strftime("%Y-%m-%d")
        try:
            exibicao = datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            data_iso = datetime.fromtimestamp(
                os.path.getmtime(caminho)).strftime("%Y-%m-%d")
            exibicao = datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
        itens.append({
            "arquivo": nome,
            "extensao": os.path.splitext(nome)[1].lower().lstrip("."),
            "atualizado": exibicao,
            "data_iso": data_iso,
            "data_manual": bool(registro.get("data")),
            "observacao": registro.get("observacao", ""),
            "tamanho_kb": round(os.path.getsize(caminho) / 1024),
            "versoes": len(registro.get("versoes", [])),
        })
    padrao = (departamento(codigo) or {}).get("modelo_padrao")
    itens.sort(key=lambda m: (m["arquivo"] != padrao, m["arquivo"]))
    return itens


def execucoes_ordenadas(codigo=None):
    itens = [e for e in EXECUCOES.values()
             if codigo is None or e["departamento"] == codigo]
    return sorted(itens, key=lambda e: e["criado_em_iso"], reverse=True)


def _validar_execucao(execucao_id, exigir_concluido=True):
    execucao = EXECUCOES.get(execucao_id)
    if not execucao:
        abort(404)
    if execucao["departamento"] not in codigos_departamento():
        abort(403)
    if exigir_concluido and (execucao.get("status") != "concluido"
                             or not execucao.get("pasta_resultado")):
        abort(409)
    return execucao


@app.route("/")
def pagina_comparar():
    codigo = dep_da_requisicao()
    return render_template("comparar.html",
                           pagina="comparar",
                           departamentos=departamentos_ativos(),
                           departamento=codigo,
                           nome_dep=nome_departamento(codigo),
                           modelos=modelos_do_departamento(codigo),
                           execucoes=execucoes_ordenadas(codigo)[:15],
                           cfg_extensoes=CFG_WEB["extensoes_aceitas"],
                           docusign_ativo=cfg_docusign(codigo).get("ativo", False))


@app.route("/api/modelos/<codigo>")
def api_modelos(codigo):
    if codigo not in codigos_departamento():
        abort(404)
    return jsonify(modelos_do_departamento(codigo))


def criar_execucao(codigo, caminho_modelo, modelo, enviado_nome, salvar_em,
                   fornecedor="", extra=None):
    """Registra uma execucao na fila (arquivo ja salvo em `salvar_em`)."""
    agora = datetime.now()
    execucao_id = os.path.basename(os.path.dirname(salvar_em))
    execucao = {
        "id": execucao_id,
        "departamento": codigo,
        "nome_departamento": nome_departamento(codigo),
        "arquivo": enviado_nome,
        "caminho_arquivo": salvar_em,
        "pasta_upload": os.path.dirname(salvar_em),
        "caminho_modelo": caminho_modelo,
        "modelo": modelo,
        "fornecedor_forcado": fornecedor,
        "fornecedor": fornecedor or "\u2014",
        "status": "na_fila",
        "status_revisao": "pendente",
        "criado_em": agora.strftime("%d/%m/%Y %H:%M"),
        "criado_em_iso": agora.isoformat(),
    }
    execucao.update(extra or {})
    salvar_execucao(execucao)
    FILA.put(execucao_id)
    return execucao


@app.route("/comparar", methods=["POST"])
def comparar():
    codigo = request.form.get("departamento", "")
    modelo = request.form.get("modelo", "")
    fornecedor = (request.form.get("fornecedor") or "").strip()[:120]
    arquivos = request.files.getlist("arquivos")

    if codigo not in codigos_departamento(so_ativos=True):
        return jsonify({"erro": "Departamento inv\u00e1lido."}), 400
    caminho_modelo = os.path.join(PASTA_MODELOS, codigo, secure_filename(modelo))
    if not modelo or not secure_filename(modelo) or not os.path.isfile(caminho_modelo):
        return jsonify({"erro": "Selecione um modelo padr\u00e3o v\u00e1lido."}), 400
    arquivos = [a for a in arquivos if a and a.filename]
    if not arquivos:
        return jsonify({"erro": "Anexe ao menos um arquivo."}), 400

    forcar = request.form.get("forcar") == "1"
    resposta = enfileirar_arquivos(codigo, caminho_modelo, secure_filename(modelo), arquivos,
                                   fornecedor, forcar)
    if not resposta["ids"] and not resposta["duplicados"]:
        return jsonify({"erro": "Nenhum arquivo com extens\u00e3o aceita."}), 400
    return jsonify(resposta)


def buscar_duplicado(hash_entrada):
    """Execucao com o mesmo hash: concluida (com resultado disponivel) ou ainda na
    fila/processando (envios repetidos em sequencia)."""
    if not hash_entrada:
        return None
    for e in sorted(EXECUCOES.values(), key=lambda x: x.get("criado_em_iso", ""),
                    reverse=True):
        if e.get("hash_entrada") != hash_entrada or e.get("arquivado"):
            continue
        if e.get("status") in ("na_fila", "processando"):
            return e
        if (e.get("status") == "concluido"
                and os.path.isfile(os.path.join(pasta_resultado(e), "resultado.json"))):
            return e
    return None


def enfileirar_arquivos(codigo, caminho_modelo, modelo, arquivos, fornecedor="",
                        forcar=False):
    """Salva e enfileira os arquivos; devolve ids criados e duplicados encontrados
    (mesmo modelo + devolucao + versao do motor + config => resultado existente)."""
    extensoes = [e.lower() for e in CFG_WEB["extensoes_aceitas"]]
    validos = [a for a in arquivos
               if secure_filename(a.filename or "")
               and os.path.splitext(secure_filename(a.filename))[1].lower() in extensoes]
    lote = uuid.uuid4().hex[:12] if len(validos) > 1 else None
    criadas, duplicados = [], []
    cfg = cfg_motor(codigo, "")
    cfg["fornecedor_forcado"] = fornecedor
    for enviado in validos:
        nome = secure_filename(enviado.filename)
        conteudo = enviado.read()
        hash_novo = motor.hash_entrada(caminho_modelo, None, cfg, conteudo_devolucao=conteudo)
        existente = None if forcar else buscar_duplicado(hash_novo)
        if existente:
            duplicados.append({"arquivo": nome, "existente": existente["id"],
                               "criado_em": existente.get("criado_em"),
                               "fornecedor": existente.get("fornecedor"),
                               "status": existente.get("status")})
            continue
        execucao_id = uuid.uuid4().hex
        pasta_upload = os.path.join(PASTA_UPLOADS, codigo, execucao_id)
        os.makedirs(pasta_upload, exist_ok=True)
        destino = os.path.join(pasta_upload, nome)
        with open(destino, "wb") as f:
            f.write(conteudo)
        extra = {"lote": lote} if lote else {}
        extra["hash_entrada"] = hash_novo
        if forcar:
            extra["forcado"] = True
        criar_execucao(codigo, caminho_modelo, modelo, nome, destino, fornecedor, extra)
        criadas.append(execucao_id)
    return {"ids": criadas, "duplicados": duplicados, "lote": lote if criadas else None}


@app.route("/api/status")
def api_status():
    ids = [i for i in (request.args.get("ids") or "").split(",") if i][:100]
    saida = []
    for execucao_id in ids:
        execucao = EXECUCOES.get(execucao_id)
        if not execucao:
            continue
        saida.append({k: execucao.get(k) for k in (
            "id", "arquivo", "fornecedor", "status", "alterados", "incluidos",
            "removidos", "movidos", "preenchidos", "pendentes", "criticos", "total",
            "erro", "paragrafos_identicos", "modelo", "criado_em", "risco_geral",
            "selo_risco", "devolucao_incompleta", "reaproveitado_de")})
    return jsonify(saida)


@app.route("/resultado/<execucao_id>")
def pagina_resultado(execucao_id):
    """Pagina cheia do resultado: comparativo + acoes enxutas + painel de IA lateral."""
    execucao = _validar_execucao(execucao_id)
    pasta = pasta_resultado(execucao)
    codigo = execucao["departamento"]
    formatos = [("pdf", "PDF (comparativo)"), ("html", "HTML interativo"),
                ("xlsx", "Excel (alterações)"), ("txt", "TXT (resumo)"),
                ("json", "JSON (resultado)"), ("redline", "Contraproposta .docx"),
                ("parecer_pdf", "Parecer PDF"), ("parecer_docx", "Parecer DOCX")]
    downloads = [(rotulo, url_for("baixar", execucao_id=execucao_id, formato=f))
                 for f, rotulo in formatos
                 if os.path.isfile(os.path.join(pasta, ARQUIVOS_SAIDA[f][0]))]
    downloads.append(("Tudo (.zip)", url_for("baixar_zip", execucao_id=execucao_id)))
    return render_template("resultado.html", pagina="comparar", pagina_cheia=True,
                           departamentos=departamentos_ativos(), departamento=codigo,
                           nome_dep=nome_departamento(codigo), execucao=execucao,
                           downloads=downloads, status_revisao=STATUS_REVISAO,
                           tem_analise_ia=os.path.isfile(os.path.join(pasta, "analise_ia.json")),
                           ia_anonimizar=(CFG_WEB.get("ia") or {}).get("anonimizar", True),
                           docusign_ativo=cfg_docusign(codigo).get("ativo", False))


@app.route("/resultado/<execucao_id>/visualizar")
def visualizar(execucao_id):
    execucao = _validar_execucao(execucao_id)
    caminho = os.path.join(pasta_resultado(execucao), "comparativo.html")
    if not os.path.exists(caminho):
        abort(404)
    with open(caminho, "r", encoding="utf-8") as f:
        html = f.read()
    if request.args.get("embutido") == "1":
        # dentro do sistema: cores do perfil atual (o arquivo baixado fica como foi gerado)
        estilo = "<style>%s</style>" % identidade_mod.css_variaveis(tokens_identidade())
        html = html.replace("</head>", estilo + "</head>", 1)
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.route("/resultado/<execucao_id>/baixar/<formato>")
def baixar(execucao_id, formato):
    execucao = _validar_execucao(execucao_id)
    if formato not in ARQUIVOS_SAIDA:
        abort(404)
    nome, tipo = ARQUIVOS_SAIDA[formato]
    caminho = os.path.join(pasta_resultado(execucao), nome)
    if not os.path.exists(caminho):
        abort(404)
    base = re.sub(r"[^\w\-]+", "_", execucao.get("fornecedor", "resultado"))[:40]
    return send_file(caminho, as_attachment=True, mimetype=tipo,
                     download_name="%s_%s" % (base, nome))


@app.route("/resultado/<execucao_id>/zip")
def baixar_zip(execucao_id):
    execucao = _validar_execucao(execucao_id)
    memoria = io.BytesIO()
    pasta = pasta_resultado(execucao)
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as z:
        for raiz, _, arquivos in os.walk(pasta):
            for nome in arquivos:
                if nome.endswith(".tmp"):
                    continue
                completo = os.path.join(raiz, nome)
                z.write(completo, os.path.relpath(completo, pasta))
    memoria.seek(0)
    base = re.sub(r"[^\w\-]+", "_", execucao.get("fornecedor", "resultado"))[:40]
    return send_file(memoria, as_attachment=True, mimetype="application/zip",
                     download_name="%s_comparativo.zip" % base)


# ---------------------------------------------------------------- revisao

RE_CHAVE_OC = re.compile(r"^[0-9a-f]{6,40}$")
STATUS_OC = ("aceito", "rejeitado")


def _ler_json(caminho, padrao=None):
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def gravar_json_atomico(caminho, dados):
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    os.replace(tmp, caminho)


def chaves_do_resultado(pasta):
    resultado = _ler_json(os.path.join(pasta, "resultado.json"))
    if not resultado:
        return None
    return {o["chave"] for o in resultado.get("ocorrencias", []) if o.get("chave")}


def validar_revisao(corpo, chaves_validas):
    """Limpa o que veio do navegador. Levanta ValueError se o formato for invalido."""
    if not isinstance(corpo, dict) or not isinstance(corpo.get("itens"), dict):
        raise ValueError("Formato de revis\u00e3o inv\u00e1lido.")
    if len(corpo["itens"]) > 5000:
        raise ValueError("Revis\u00e3o grande demais.")
    itens = {}
    for chave, item in corpo["itens"].items():
        if not isinstance(chave, str) or not RE_CHAVE_OC.match(chave):
            continue
        if chaves_validas is not None and chave not in chaves_validas:
            continue
        if not isinstance(item, dict):
            continue
        limpo = {}
        if item.get("status") in STATUS_OC:
            limpo["status"] = item["status"]
        comentario = item.get("comentario")
        if isinstance(comentario, str) and comentario.strip():
            limpo["comentario"] = comentario.strip()[:4000]
        if not limpo:
            continue
        por = item.get("por")
        if isinstance(por, str) and por.strip():
            limpo["por"] = por.strip()[:120]
        limpo["em"] = str(item.get("em") or datetime.now().isoformat())[:40]
        itens[chave] = limpo
    return {"versao": 1, "itens": itens, "atualizado_em": datetime.now().isoformat(
        timespec="seconds")}


def resumo_revisao(revisao, total):
    itens = (revisao or {}).get("itens", {})
    return {"revisadas": sum(1 for i in itens.values() if i.get("status")),
            "aceitas": sum(1 for i in itens.values() if i.get("status") == "aceito"),
            "rejeitadas": sum(1 for i in itens.values() if i.get("status") == "rejeitado"),
            "comentadas": sum(1 for i in itens.values() if i.get("comentario")),
            "total": total}


def ao_salvar_revisao(execucao, revisao):
    """Trechos aceitos entram na biblioteca de clausulas aprovadas do departamento."""
    resultado = _ler_json(os.path.join(pasta_resultado(execucao), "resultado.json"))
    execucao["biblioteca"] = recursos.atualizar_biblioteca(PASTA_DADOS, execucao, resultado,
                                                          revisao)


@app.route("/resultado/<execucao_id>/revisao", methods=["GET", "PUT"])
def api_revisao(execucao_id):
    execucao = _validar_execucao(execucao_id)
    pasta = pasta_resultado(execucao)
    caminho = os.path.join(pasta, "revisao.json")
    if request.method == "GET":
        return jsonify({"ok": True, "revisao": _ler_json(caminho, {"versao": 1, "itens": {}})})
    if request.content_length and request.content_length > 2 * 1024 * 1024:
        return jsonify({"ok": False, "erro": "Revis\u00e3o grande demais."}), 413
    chaves = chaves_do_resultado(pasta)
    try:
        revisao = validar_revisao(request.get_json(silent=True), chaves)
    except ValueError as erro:
        return jsonify({"ok": False, "erro": str(erro)}), 400
    with _trava:
        gravar_json_atomico(caminho, revisao)
    total = len(chaves) if chaves is not None else 0
    execucao["revisao"] = resumo_revisao(revisao, total)
    if execucao.get("status_revisao", "pendente") == "pendente" and revisao["itens"]:
        execucao["status_revisao"] = "em_revisao"
    ao_salvar_revisao(execucao, revisao)
    salvar_execucao(execucao)
    return jsonify({"ok": True, "resumo": execucao["revisao"]})


# ---------------------------------------------------------------- paginas

@app.route("/modelos")
def pagina_modelos():
    codigo = dep_da_requisicao()
    return render_template("modelos.html", pagina="modelos",
                           departamentos=departamentos_visiveis(),
                           departamento=codigo,
                           nome_dep=nome_departamento(codigo),
                           modelos=modelos_do_departamento(codigo))


@app.route("/modelos/upload", methods=["POST"])
def upload_modelo():
    codigo = request.form.get("departamento", "")
    if codigo not in codigos_departamento():
        abort(400)
    enviado = request.files.get("modelo")
    if enviado and enviado.filename:
        nome = secure_filename(enviado.filename)
        extensoes = [e.lower() for e in CFG_WEB["extensoes_aceitas"]]
        if nome and os.path.splitext(nome)[1].lower() in extensoes:
            registrar_nova_versao_modelo(codigo, nome, enviado)
    return redirect(url_for("pagina_modelos", dep=codigo))


def registrar_nova_versao_modelo(codigo, nome, enviado):
    """Salva o modelo; se ja existir, a versao anterior vai para _versoes/."""
    pasta = os.path.join(PASTA_MODELOS, codigo)
    meta = ler_meta(codigo)
    if os.path.isfile(os.path.join(pasta, nome)):
        recursos.arquivar_versao(pasta, nome, meta)
        gravar_meta(codigo, meta)
    tmp = os.path.join(pasta, nome + ".tmp")
    enviado.save(tmp)
    os.replace(tmp, os.path.join(pasta, nome))


@app.route("/modelos/excluir", methods=["POST"])
def excluir_modelo():
    codigo = request.form.get("departamento", "")
    nome = secure_filename(request.form.get("arquivo", ""))
    if codigo in codigos_departamento() and nome:
        caminho = os.path.join(PASTA_MODELOS, codigo, nome)
        if os.path.isfile(caminho):
            lixeira = os.path.join(PASTA_MODELOS, codigo, "_removidos")
            os.makedirs(lixeira, exist_ok=True)
            shutil.move(caminho, os.path.join(
                lixeira, "%s_%s" % (datetime.now().strftime("%Y%m%d%H%M"), nome)))
    return redirect(url_for("pagina_modelos", dep=codigo))


@app.route("/modelos/anotar", methods=["POST"])
def anotar_modelo():
    codigo = request.form.get("departamento", "")
    _, nome = caminho_modelo_seguro(codigo, request.form.get("arquivo", ""))
    meta = ler_meta(codigo)
    registro = meta.get(nome, {})
    registro["observacao"] = (request.form.get("observacao") or "").strip()[:600]
    data = (request.form.get("data") or "").strip()
    if data:
        try:
            datetime.strptime(data, "%Y-%m-%d")
            registro["data"] = data
        except ValueError:
            pass
    else:
        registro.pop("data", None)
    meta[nome] = registro
    gravar_meta(codigo, meta)
    return redirect(url_for("pagina_modelos", dep=codigo))


@app.route("/modelos/arquivo/<codigo>/<arquivo>")
def arquivo_modelo(codigo, arquivo):
    """Serve o modelo em si: inline para leitura, anexo para download."""
    caminho, nome = caminho_modelo_seguro(codigo, arquivo)
    anexo = request.args.get("baixar") == "1"
    return send_file(caminho, as_attachment=anexo, download_name=nome)


@app.route("/modelos/ver/<codigo>/<arquivo>")
def ver_modelo(codigo, arquivo):
    """Leitura do contrato-modelo dentro do sistema."""
    caminho, nome = caminho_modelo_seguro(codigo, arquivo)
    extensao = os.path.splitext(nome)[1].lower()
    paragrafos, erro = [], None
    if extensao != ".pdf":
        try:
            paragrafos = motor.ler_documento(caminho)
        except Exception as falha:  # noqa: BLE001
            erro = str(falha)
    registro = ler_meta(codigo).get(nome, {})
    tema = request.args.get("tema") if request.args.get("tema") in ("claro", "escuro") \
        else "claro"
    if request.args.get("embutido") == "1":
        return render_template("ler_modelo_embutido.html",
                               departamento=codigo, arquivo=nome,
                               extensao=extensao.lstrip("."),
                               paragrafos=paragrafos, erro=erro, tema=tema)
    return render_template("ler_modelo.html", pagina="modelos",
                           departamentos=CFG_WEB["departamentos"],
                           departamento=codigo,
                           nome_dep=nome_departamento(codigo),
                           arquivo=nome, extensao=extensao.lstrip("."),
                           paragrafos=paragrafos, erro=erro,
                           observacao=registro.get("observacao", ""))


# ---------------------------------------------------------------- assinatura

@app.route("/resultado/<execucao_id>/assinatura/enviar", methods=["POST"])
def enviar_para_assinatura(execucao_id):
    execucao = _validar_execucao(execucao_id)
    if not DOCUSIGN_DISPONIVEL:
        return jsonify({"erro": "Biblioteca da DocuSign n\u00e3o instalada no "
                        "servidor (pip install docusign-esign)."}), 500

    cfg = cfg_docusign(execucao["departamento"])
    if not cfg.get("ativo"):
        return jsonify({"erro": "Assinatura eletr\u00f4nica n\u00e3o configurada para o "
                        "departamento %s. Veja Ajustes > Assinatura."
                        % execucao["nome_departamento"]}), 400

    corpo = request.get_json(silent=True) or {}
    signatarios = []
    for item in (corpo.get("signatarios") or [])[:10]:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("nome") or "").strip()[:120]
        email = str(item.get("email") or "").strip()[:200]
        if not nome or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            continue
        try:
            ordem = int(item.get("ordem") or 1)
        except (TypeError, ValueError):
            ordem = 1
        signatarios.append({"nome": nome, "email": email, "ordem": max(1, min(ordem, 10))})

    if not signatarios:
        return jsonify({"erro": "Informe nome e e-mail v\u00e1lidos de ao menos um "
                        "signat\u00e1rio."}), 400

    variaveis = {"fornecedor": execucao.get("fornecedor", ""),
                 "departamento": execucao.get("nome_departamento", ""),
                 "arquivo": execucao.get("arquivo", ""),
                 "data": datetime.now().strftime("%d/%m/%Y")}
    assunto = configuracao.formatar_mensagem(
        cfg.get("assunto_padrao", DOCUSIGN_PADRAO_DEPTO["assunto_padrao"]), **variaveis)
    mensagem = configuracao.formatar_mensagem(cfg.get("mensagem_padrao", ""), **variaveis)

    try:
        envelope_id = docusign.enviar_envelope(
            cfg, resolver_caminho_dados(execucao["caminho_arquivo"]), assunto, signatarios,
            mensagem)
    except docusign.ErroDocuSign as erro:
        return jsonify({"erro": str(erro)}), 502

    execucao.update({
        "docusign_envelope_id": envelope_id,
        "docusign_status": "enviado",
        "docusign_signatarios": signatarios,
        "docusign_enviado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
    })
    salvar_execucao(execucao)
    return jsonify({"ok": True, "envelope_id": envelope_id, "status": "enviado"})


@app.route("/api/assinatura/status/<execucao_id>")
def status_assinatura(execucao_id):
    # mesma validacao das demais rotas de resultado (departamento incluido)
    execucao = _validar_execucao(execucao_id)
    if not execucao.get("docusign_envelope_id"):
        abort(404)
    if not DOCUSIGN_DISPONIVEL:
        return jsonify({"status": execucao.get("docusign_status", "desconhecido")})

    cfg = cfg_docusign(execucao["departamento"])
    try:
        status_atual = docusign.consultar_status(cfg, execucao["docusign_envelope_id"])
    except docusign.ErroDocuSign as erro:
        return jsonify({"status": execucao.get("docusign_status", "desconhecido"),
                        "erro": str(erro)})

    if status_atual != execucao.get("docusign_status"):
        execucao["docusign_status"] = status_atual
        salvar_execucao(execucao)
    return jsonify({"status": status_atual,
                    "envelope_id": execucao["docusign_envelope_id"],
                    "enviado_em": execucao.get("docusign_enviado_em")})


# ---------------------------------------------------------------- ajustes

ABAS_AJUSTES = [("identidade", "Identidade"), ("departamentos", "Departamentos"),
                ("analise", "Regras de an\u00e1lise"), ("playbook", "Playbook"),
                ("saidas", "Sa\u00eddas"), ("armazenamento", "Armazenamento"),
                ("assinatura", "Assinatura eletr\u00f4nica"), ("ia", "IA"),
                ("sistema", "Sistema")]


def _exigir_json_mesma_origem():
    """Bloqueia envios de formularios de outros sites (so fetch com cabecalho)."""
    if request.headers.get("X-Requested-With") != "fetch":
        abort(403)


def _salvar_config(nova, mensagem="Salvo e aplicado sem reiniciar."):
    configuracao.gravar(CAMINHO_CONFIG_WEB, nova, PASTA_BACKUPS)
    aplicar_config(nova)
    return jsonify({"ok": True, "mensagem": mensagem})


def modelos_por_departamento():
    return {c: [m["arquivo"] for m in modelos_do_departamento(c)]
            for c in codigos_departamento()}


def diagnostico():
    bibliotecas = {}
    for nome, modulo in (("Flask", "flask"), ("python-docx", "docx"),
                         ("openpyxl", "openpyxl"), ("xhtml2pdf", "xhtml2pdf"),
                         ("pdfplumber", "pdfplumber"), ("pypdf", "pypdf"),
                         ("docusign-esign", "docusign_esign"), ("waitress", "waitress"),
                         ("lxml", "lxml")):
        try:
            mod = __import__(modulo)
            bibliotecas[nome] = getattr(mod, "__version__", "instalado")
        except Exception:  # noqa: BLE001
            bibliotecas[nome] = None
    try:
        uso = shutil.disk_usage(PASTA_DADOS)
        disco = {"livre_gb": round(uso.free / 1e9, 1), "total_gb": round(uso.total / 1e9, 1)}
    except OSError:
        disco = None
    return {
        "python": platform.python_version(),
        "python_ok": sys.version_info >= (3, 10),
        "sistema": platform.platform(terse=True),
        "bibliotecas": bibliotecas,
        "disco": disco,
        "fila": FILA.qsize(),
        "processando": sum(1 for e in EXECUCOES.values() if e.get("status") == "processando"),
        "execucoes": len(EXECUCOES),
    }


@app.route("/ajustes")
def pagina_ajustes():
    codigo = dep_da_requisicao()
    abas = [a for a in ABAS_AJUSTES if a[0] != "assinatura" or DOCUSIGN_DISPONIVEL]
    aba = request.args.get("aba") if request.args.get("aba") in dict(abas) \
        else "identidade"
    escopo = request.args.get("escopo") or "global"
    if escopo != "global" and escopo not in codigos_departamento():
        escopo = "global"
    regras, origem = regras_analise(escopo if escopo != "global" else "")
    if escopo == "global":
        for chave, valor in ((CFG_WEB.get("analise") or {}).get("global") or {}).items():
            regras[chave], origem[chave] = valor, "global"
    publica = configuracao.sem_segredos(CFG_WEB)
    tokens = tokens_identidade()
    extras = {}
    if aba == "identidade":
        extras = {"perfis": perfis_identidade(), "perfil_ativo": perfil_ativo()}
    elif aba == "ia":
        conexao = analise_ia.carregar_conexao()
        extras = {"conexao": analise_ia.resumo_conexao(conexao),
                  "pendencias_ia": pendencias_ia(conexao),
                  "ia_habilitada": analise_ia.habilitada(CFG_WEB, conexao),
                  "uso_ia": uso_ia_recente(), "provedores": analise_ia.PROVEDORES,
                  "esforcos": analise_ia.ESFORCOS, "recursos_ia": configuracao.RECURSOS_IA,
                  "rigores": configuracao.RIGORES_FILTRO, "limites_ia": configuracao.LIMITES_IA}
    return render_template(
        "ajustes.html", pagina="ajustes", aba=aba, abas=abas,
        departamentos=departamentos_visiveis(), departamento=codigo,
        departamentos_excluidos=[d for d in CFG_WEB["departamentos"] if d.get("excluido")],
        nome_dep=nome_departamento(codigo), cfg=publica, pasta_dados=PASTA_DADOS,
        caminho_config=CAMINHO_CONFIG_WEB, versao_motor=motor.VERSAO,
        docusign_disponivel=DOCUSIGN_DISPONIVEL,
        motivo_docusign=_MOTIVO_DOCUSIGN_INDISPONIVEL,
        segredos=configuracao.status_segredos(CFG_WEB),
        escopo=escopo, regras=regras, origem=origem,
        tipos=configuracao.TIPOS, campos_playbook=motor.CAMPOS_PLAYBOOK,
        operadores=motor.OPERADORES_PLAYBOOK, formatos_data=configuracao.FORMATOS_DATA,
        formatos_hora=configuracao.FORMATOS_HORA, visoes=configuracao.VISOES,
        icones=configuracao.ICONES, extensoes_possiveis=configuracao.EXTENSOES_POSSIVEIS,
        funcoes_ia=configuracao.FUNCOES_IA, variaveis=configuracao.VARIAVEIS_MENSAGEM,
        modelos_por_dep=modelos_por_departamento(),
        avisos_contraste=tokens["avisos"], contraste=tokens["contraste"],
        backups=configuracao.listar_backups(PASTA_BACKUPS)[:30],
        diag=diagnostico() if aba == "sistema" else None,
        logo_proprio=bool(caminho_logo_configurado()), **extras)


@app.route("/api/ajustes/<secao>", methods=["POST"])
def api_salvar_ajustes(secao):
    _exigir_json_mesma_origem()
    if secao not in configuracao.VALIDADORES:
        abort(404)
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"ok": False, "erros": {"_": "Dados inv\u00e1lidos."}}), 400
    atual = copy.deepcopy(CFG_WEB)
    if secao == "departamentos":
        parte, erros = configuracao.validar_departamentos(dados, atual,
                                                          modelos_por_departamento(),
                                                          departamentos_com_dados())
    elif secao == "ia":
        parte, erros = configuracao.validar_ia(dados, atual, pendencias_ia())
    else:
        parte, erros = configuracao.VALIDADORES[secao](dados, atual)
    if erros or parte is None:
        return jsonify({"ok": False, "erros": erros or {"_": "Dados inv\u00e1lidos."}}), 400
    atual.update(parte)
    configuracao.completar(atual)
    if secao == "departamentos":
        for d in atual["departamentos"]:
            atual["docusign"].setdefault(d["codigo"], dict(DOCUSIGN_PADRAO_DEPTO))
    return _salvar_config(atual)


@app.route("/api/ajustes/tokens")
def api_tokens():
    cor = request.args.get("cor", "")
    if not identidade_mod.cor_valida(cor):
        return jsonify({"ok": False, "erro": "Cor inv\u00e1lida."}), 400
    t = identidade_mod.gerar_tokens(cor)
    return jsonify({"ok": True, "css": identidade_mod.css_variaveis(t), "avisos": t["avisos"],
                    "contraste": t["contraste"], "claro": t["claro"], "escuro": t["escuro"]})


LIMITE_LOGO = 512 * 1024
RE_SVG_PERIGOSO = re.compile(r"<\s*script|on[a-z]+\s*=|javascript:|<\s*foreignObject|"
                             r"<!ENTITY|xlink:href\s*=\s*[\"'](?!#)|href\s*=\s*[\"']\s*(?!#)",
                             re.IGNORECASE)


@app.route("/api/ajustes/identidade/logo", methods=["POST"])
def api_logo():
    _exigir_json_mesma_origem()
    enviado = request.files.get("logo")
    if not enviado or not enviado.filename:
        return jsonify({"ok": False, "erros": {"logo": "Escolha um arquivo."}}), 400
    extensao = os.path.splitext(enviado.filename)[1].lower()
    conteudo = enviado.read(LIMITE_LOGO + 1)
    if len(conteudo) > LIMITE_LOGO:
        return jsonify({"ok": False, "erros": {"logo": "Logo acima de 512 KB."}}), 400
    if extensao == ".png":
        if not conteudo.startswith(b"\x89PNG\r\n\x1a\n"):
            return jsonify({"ok": False, "erros": {"logo": "Arquivo n\u00e3o \u00e9 PNG."}}), 400
    elif extensao == ".svg":
        texto = conteudo.decode("utf-8", "ignore")
        if "<svg" not in texto.lower() or RE_SVG_PERIGOSO.search(texto):
            return jsonify({"ok": False, "erros": {"logo": "SVG inv\u00e1lido ou com "
                                                            "conte\u00fado ativo (scripts, "
                                                            "links externos)."}}), 400
    else:
        return jsonify({"ok": False, "erros": {"logo": "Use .svg ou .png."}}), 400
    os.makedirs(PASTA_IDENTIDADE, exist_ok=True)
    nome = "logo_%s%s" % (datetime.now().strftime("%Y%m%d%H%M%S"), extensao)
    caminho = os.path.join(PASTA_IDENTIDADE, nome)
    with open(caminho + ".tmp", "wb") as f:
        f.write(conteudo)
    os.replace(caminho + ".tmp", caminho)
    nova = copy.deepcopy(CFG_WEB)
    nova["identidade"]["logo"] = "identidade/" + nome
    nova.setdefault("identidade_perfis", {})[perfil_ativo()] = dict(nova["identidade"])
    return _salvar_config(nova, "Logo atualizado.")


@app.route("/api/ajustes/perfil", methods=["POST"])
def api_trocar_perfil():
    """Troca o perfil de identidade ativo (ou restaura o padrao do perfil)."""
    _exigir_json_mesma_origem()
    dados = request.get_json(silent=True) or {}
    alvo = str(dados.get("perfil") or "")
    perfis = {p["id"]: p for p in perfis_identidade()}
    if alvo not in perfis:
        return jsonify({"ok": False, "erros": {"perfil": "Perfil desconhecido."}}), 400
    nova = copy.deepcopy(CFG_WEB)
    guardados = nova.setdefault("identidade_perfis", {})
    guardados[perfil_ativo()] = dict(nova.get("identidade") or {})    # guarda o atual
    if configuracao._bool(dados.get("restaurar")):
        guardados.pop(alvo, None)
    nova["identidade"] = dict(guardados.get(alvo) or perfis[alvo]["identidade"])
    nova["perfil_identidade"] = alvo
    guardados[alvo] = dict(nova["identidade"])
    mensagem = ("Perfil “%s” restaurado ao padrão." if configuracao._bool(dados.get("restaurar"))
                else "Perfil “%s” aplicado.") % perfis[alvo]["rotulo"]
    return _salvar_config(nova, mensagem)


@app.route("/api/ajustes/ia_conexao", methods=["POST"])
def api_ia_conexao():
    """Grava provedor/endpoint/deployment/chave em ia_config/config.json."""
    _exigir_json_mesma_origem()
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"ok": False, "erros": {"_": "Dados inv\u00e1lidos."}}), 400
    conexao = analise_ia.carregar_conexao()
    if conexao.get("_erro"):
        return jsonify({"ok": False, "erros": {"_": conexao["_erro"] + " Corrija o arquivo "
                                               "antes de salvar pela tela."}}), 400
    nova, erros = configuracao.validar_ia_conexao(dados, conexao)
    if erros or nova is None:
        return jsonify({"ok": False, "erros": erros}), 400
    # chave vinda de variavel de ambiente nao e gravada no arquivo
    for prov, variavel in analise_ia.VARIAVEIS_CHAVE.items():
        if os.environ.get(variavel) and nova[prov]["chave_api"] == os.environ[variavel].strip():
            nova[prov]["chave_api"] = ""
    analise_ia.gravar_conexao(nova)
    return jsonify({"ok": True, "mensagem": "Conexão gravada em ia_config/config.json."})


@app.route("/api/ajustes/ia_testar", methods=["POST"])
def api_ia_testar():
    _exigir_json_mesma_origem()
    conexao = analise_ia.carregar_conexao()
    if conexao.get("_erro"):
        return jsonify({"ok": False, "mensagem": conexao["_erro"]})
    ok, mensagem = analise_ia.testar_conexao(conexao)
    return jsonify({"ok": ok, "mensagem": mensagem})


@app.route("/api/ajustes/assinatura/chave", methods=["POST"])
def api_chave_docusign():
    _exigir_json_mesma_origem()
    codigo = request.form.get("departamento", "")
    if codigo not in codigos_departamento():
        abort(400)
    enviado = request.files.get("chave")
    conteudo = enviado.read(16 * 1024 + 1) if enviado else b""
    if (not conteudo or len(conteudo) > 16 * 1024
            or b"PRIVATE KEY-----" not in conteudo):
        return jsonify({"ok": False, "erros": {"chave": "Envie a chave privada RSA (.pem)."}}), 400
    os.makedirs(PASTA_SEGREDOS, exist_ok=True)
    caminho = os.path.join(PASTA_SEGREDOS, "docusign_%s.pem" % codigo)
    with open(caminho + ".tmp", "wb") as f:
        f.write(conteudo)
    os.replace(caminho + ".tmp", caminho)
    try:
        os.chmod(caminho, 0o600)
    except OSError:
        pass
    nova = copy.deepcopy(CFG_WEB)
    nova["docusign"].setdefault(codigo, dict(DOCUSIGN_PADRAO_DEPTO))
    nova["docusign"][codigo]["caminho_chave_privada"] = caminho
    return _salvar_config(nova, "Chave privada guardada.")


@app.route("/api/ajustes/assinatura/testar", methods=["POST"])
def api_testar_docusign():
    _exigir_json_mesma_origem()
    codigo = (request.get_json(silent=True) or {}).get("departamento")
    if codigo not in codigos_departamento():
        abort(400)
    if not DOCUSIGN_DISPONIVEL:
        return jsonify({"ok": False, "mensagem": "Biblioteca docusign-esign n\u00e3o instalada."})
    try:
        docusign.cliente_autenticado(cfg_docusign(codigo))
    except docusign.ErroDocuSign as erro:
        return jsonify({"ok": False, "mensagem": str(erro)[:600]})
    except Exception as erro:  # noqa: BLE001
        return jsonify({"ok": False, "mensagem": "Falha de conex\u00e3o: %s" % str(erro)[:300]})
    return jsonify({"ok": True, "mensagem": "Conex\u00e3o com a DocuSign funcionando."})


@app.route("/api/ajustes/exportar")
def api_exportar_config():
    dados = configuracao.sem_segredos(CFG_WEB)
    dados.pop("pasta_dados", None)
    corpo = json.dumps(dados, ensure_ascii=False, indent=2)
    return Response(corpo, mimetype="application/json", headers={
        "Content-Disposition": "attachment; filename=config_comparador_%s.json"
                               % datetime.now().strftime("%Y%m%d")})


@app.route("/api/ajustes/importar", methods=["POST"])
def api_importar_config():
    _exigir_json_mesma_origem()
    novo = request.get_json(silent=True)
    final, erros = configuracao.validar_importacao(novo, copy.deepcopy(CFG_WEB),
                                                    modelos_por_departamento())
    if erros or final is None:
        return jsonify({"ok": False, "erros": erros}), 400
    configuracao.completar(final)
    return _salvar_config(final, "Configura\u00e7\u00e3o importada e aplicada.")


@app.route("/api/ajustes/restaurar", methods=["POST"])
def api_restaurar_backup():
    _exigir_json_mesma_origem()
    nome = str((request.get_json(silent=True) or {}).get("arquivo") or "")
    if not configuracao.RE_BACKUP.match(nome):
        abort(400)
    caminho = os.path.join(PASTA_BACKUPS, nome)
    if not os.path.isfile(caminho):
        abort(404)
    antigo = _ler_json(caminho)
    if not isinstance(antigo, dict):
        return jsonify({"ok": False, "erros": {"_": "Backup ileg\u00edvel."}}), 400
    final, erros = configuracao.validar_importacao(antigo, copy.deepcopy(CFG_WEB),
                                                    modelos_por_departamento())
    if erros or final is None:
        return jsonify({"ok": False, "erros": erros}), 400
    # o backup pode trazer credenciais antigas do proprio servidor: mantem as atuais
    configuracao.completar(final)
    return _salvar_config(final, "Backup %s restaurado." % nome)


@app.route("/api/ajustes/diagnostico")
def api_diagnostico():
    return jsonify(diagnostico())


@app.route("/api/ajustes/retencao/<acao>", methods=["POST"])
def api_retencao(acao):
    _exigir_json_mesma_origem()
    dias = int(CFG_WEB.get("retencao_dias") or 0)
    if acao == "simular":
        return jsonify({"ok": True, **manutencao.simular(PASTA_DADOS, dias, EXECUCOES)})
    if acao == "executar":
        with _trava:
            relatorio = manutencao.executar(PASTA_DADOS, dias, EXECUCOES)
            gravar_historico()
        return jsonify({"ok": True, **relatorio})
    abort(404)



# ---------------------------------------------------------------- fase 5: revisao/rodadas

def _resultado_e_revisao(execucao):
    pasta = pasta_resultado(execucao)
    resultado = _ler_json(os.path.join(pasta, "resultado.json"))
    if not resultado:
        abort(409, "Resultado sem resultado.json (gerado pela versão 1.1). "
                   "Reprocesse a comparação.")
    revisao = _ler_json(os.path.join(pasta, "revisao.json"), {"itens": {}})
    return pasta, resultado, revisao


@app.route("/api/execucoes/<execucao_id>/status-revisao", methods=["POST"])
def api_status_revisao(execucao_id):
    _exigir_json_mesma_origem()
    execucao = _validar_execucao(execucao_id)
    dados = request.get_json(silent=True) or {}
    status = dados.get("status_revisao", execucao.get("status_revisao", "pendente"))
    if status not in STATUS_REVISAO:
        return jsonify({"ok": False, "erro": "Status inválido."}), 400
    responsavel = re.sub(r"[\x00-\x1f]", "", str(dados.get("responsavel",
                                                              execucao.get("responsavel", ""))
                                                   or "")).strip()[:120]
    anterior = execucao.get("status_revisao")
    execucao["status_revisao"] = status
    execucao["responsavel"] = responsavel
    if status in ("aprovado", "devolvido") and anterior != status:
        execucao["revisao_concluida_em_iso"] = datetime.now().isoformat(timespec="seconds")
    elif status in ("pendente", "em_revisao"):
        execucao.pop("revisao_concluida_em_iso", None)
    salvar_execucao(execucao)
    return jsonify({"ok": True, "status_revisao": status, "responsavel": responsavel})


@app.route("/rodadas/<execucao_id>")
def pagina_rodadas(execucao_id):
    execucao = _validar_execucao(execucao_id)
    grupo = recursos.rodadas_do_grupo(EXECUCOES, execucao)
    comparacoes = []
    for anterior, atual in zip(grupo, grupo[1:]):
        r_ant = _ler_json(os.path.join(pasta_resultado(anterior), "resultado.json"))
        r_atu = _ler_json(os.path.join(pasta_resultado(atual), "resultado.json"))
        comparacoes.append({"anterior": anterior, "atual": atual,
                            "classificacao": recursos.comparar_rodadas(r_ant, r_atu)
                            if r_ant and r_atu else None})
    return render_template("rodadas.html", pagina="historico",
                           departamentos=CFG_WEB["departamentos"],
                           departamento=execucao["departamento"],
                           nome_dep=nome_departamento(execucao["departamento"]),
                           execucao=execucao, grupo=grupo, comparacoes=comparacoes,
                           nomes_tipo=NOMES_TIPO)


def _pasta_rodada(execucao):
    return os.path.join(pasta_resultado(execucao), "_rodada_anterior")


@app.route("/resultado/<execucao_id>/rodada-anterior", methods=["POST"])
def gerar_rodada_anterior(execucao_id):
    """Compara a devolucao desta rodada com a da rodada anterior (texto x texto)."""
    _exigir_json_mesma_origem()
    execucao = _validar_execucao(execucao_id)
    grupo = recursos.rodadas_do_grupo(EXECUCOES, execucao)
    posicao = [e["id"] for e in grupo].index(execucao["id"])
    if posicao == 0:
        return jsonify({"ok": False, "erro": "Esta é a primeira rodada."}), 400
    anterior = grupo[posicao - 1]
    cfg = cfg_motor(execucao["departamento"], "")
    cfg.update({"fornecedor_forcado": execucao.get("fornecedor", ""), "playbook": [],
                "biblioteca_aprovada": {}})
    try:
        resultado = motor.analisar(resolver_caminho_dados(anterior["caminho_arquivo"]),
                                   resolver_caminho_dados(execucao["caminho_arquivo"]), cfg)
    except Exception as erro:  # noqa: BLE001
        return jsonify({"ok": False, "erro": "Falha ao comparar rodadas: %s" % erro}), 500
    resultado["meta"]["arquivo_modelo"] = "Rodada %s: %s" % (anterior.get("rodada", "?"),
                                                            anterior.get("arquivo"))
    resultado["meta"]["arquivo_devolucao"] = "Rodada %s: %s" % (execucao.get("rodada", "?"),
                                                               execucao.get("arquivo"))
    pasta = _pasta_rodada(execucao)
    os.makedirs(pasta, exist_ok=True)
    import comparativo_interativo
    gravar_json_atomico(os.path.join(pasta, "resultado.json"), resultado)
    with open(os.path.join(pasta, "comparativo.html.tmp"), "w", encoding="utf-8") as f:
        f.write(comparativo_interativo.montar(resultado, cfg))
    os.replace(os.path.join(pasta, "comparativo.html.tmp"),
               os.path.join(pasta, "comparativo.html"))
    return jsonify({"ok": True, "url": url_for("ver_rodada_anterior", execucao_id=execucao_id),
                    "total": resultado["resumo"]["TOTAL"]["total"]})


@app.route("/resultado/<execucao_id>/rodada-anterior/visualizar")
def ver_rodada_anterior(execucao_id):
    execucao = _validar_execucao(execucao_id)
    caminho = os.path.join(_pasta_rodada(execucao), "comparativo.html")
    if not os.path.isfile(caminho):
        abort(404)
    with open(caminho, encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.route("/resultado/<execucao_id>/redline", methods=["POST"])
def gerar_redline(execucao_id):
    _exigir_json_mesma_origem()
    execucao = _validar_execucao(execucao_id)
    pasta, resultado, revisao = _resultado_e_revisao(execucao)
    ident = identidade_atual()
    try:
        resumo = redline.gerar(resultado, revisao,
                               resolver_caminho_dados(execucao["caminho_arquivo"]),
                               os.path.join(pasta, redline.NOME_ARQUIVO),
                               resolver_caminho_dados(execucao.get("caminho_modelo")),
                               autor="Revisão %s" % ident["organizacao_contratante"])
    except redline.ErroRedline as erro:
        return jsonify({"ok": False, "erro": str(erro)}), 409
    execucao["redline_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    salvar_execucao(execucao)
    return jsonify({"ok": True, "resumo": resumo,
                    "url": url_for("baixar", execucao_id=execucao_id, formato="redline")})


@app.route("/resultado/<execucao_id>/parecer", methods=["POST"])
def gerar_parecer(execucao_id):
    _exigir_json_mesma_origem()
    execucao = _validar_execucao(execucao_id)
    pasta, resultado, revisao = _resultado_e_revisao(execucao)
    observacao = str((request.get_json(silent=True) or {}).get("observacao") or "")[:3000]
    cfg = {"identidade": identidade_atual(), "caminho_logo": caminho_logo_configurado() or ""}
    arquivos = parecer.gerar(pasta, resultado, revisao, execucao, cfg, observacao,
                             _logger_execucao(execucao_id))
    execucao["parecer_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    salvar_execucao(execucao)
    return jsonify({"ok": True, "arquivos": arquivos,
                    "pdf": url_for("baixar", execucao_id=execucao_id, formato="parecer_pdf"),
                    "docx": url_for("baixar", execucao_id=execucao_id,
                                    formato="parecer_docx")})


# ---------------------------------------------------------------- IA (sob demanda)

LIMITADOR_IA = ia_filtro.LimitadorTaxa()
LIMITE_GLOBAL_IA_MINUTO = 60                  # teto de seguranca para todas as chamadas
ARQUIVO_USO_IA = "ia_uso.json"
_trava_uso_ia = threading.Lock()


def conexao_ia():
    return analise_ia.carregar_conexao(criar=False)


def pendencias_ia(conexao=None):
    """O que falta para a IA poder ser ligada (conexao)."""
    conexao = conexao or conexao_ia()
    faltando = []
    if conexao.get("_erro"):
        faltando.append(conexao["_erro"])
    faltando.extend(analise_ia.pendencias_conexao(conexao))
    return faltando


def _caminho_uso_ia():
    return os.path.join(PASTA_DADOS, ARQUIVO_USO_IA)


def registrar_uso_ia(tipo, uso=None, cache=False, bloqueada=False):
    """Contadores por dia (sem conteudo): chamadas, tokens, cache e bloqueios."""
    hoje = datetime.now().strftime("%Y-%m-%d")
    with _trava_uso_ia:
        dados = _ler_json(_caminho_uso_ia(), {}) or {}
        dia = dados.setdefault(hoje, {"chamadas": 0, "tokens_entrada": 0, "tokens_saida": 0,
                                      "cache": 0, "bloqueadas": 0, "por_tipo": {}})
        if bloqueada:
            dia["bloqueadas"] += 1
        elif cache:
            dia["cache"] += 1
        else:
            dia["chamadas"] += 1
            dia["por_tipo"][tipo] = dia["por_tipo"].get(tipo, 0) + 1
            dia["tokens_entrada"] += int((uso or {}).get("entrada") or 0)
            dia["tokens_saida"] += int((uso or {}).get("saida") or 0)
        for antigo in sorted(dados)[:-60]:              # guarda 60 dias
            dados.pop(antigo, None)
        gravar_json_atomico(_caminho_uso_ia(), dados)


def uso_ia_recente(dias=7):
    dados = _ler_json(_caminho_uso_ia(), {}) or {}
    return [(d, dados[d]) for d in sorted(dados, reverse=True)[:dias]]


def tokens_ia_hoje():
    dia = (_ler_json(_caminho_uso_ia(), {}) or {}).get(datetime.now().strftime("%Y-%m-%d"))
    return (dia or {}).get("tokens_entrada", 0) + (dia or {}).get("tokens_saida", 0)


def _barreira_ia(recurso, chave_cliente=None, por_minuto=None):
    """Checagens comuns antes de gastar tokens. -> (resposta_de_erro | None, conexao)"""
    conexao = conexao_ia()
    if not analise_ia.recurso_ativo(CFG_WEB, recurso, conexao):
        return (jsonify({"ok": False, "erro": "Recurso de IA desligado. Veja Ajustes → IA."}),
                403), conexao
    limite_dia = int((CFG_WEB.get("ia") or {}).get("limite_tokens_dia") or 0)
    if limite_dia and tokens_ia_hoje() >= limite_dia:
        return (jsonify({"ok": False, "erro": "Limite diário de tokens atingido (%d). Ajuste "
                         "em Ajustes → IA ou tente amanhã." % limite_dia}), 429), conexao
    if chave_cliente and por_minuto:
        ok, espera = LIMITADOR_IA.permitir(chave_cliente, por_minuto, 60)
        if not ok:
            return (jsonify({"ok": False, "erro": "Muitas perguntas seguidas. Aguarde %d s."
                             % espera, "espera": espera}), 429), conexao
    ok, espera = LIMITADOR_IA.permitir("ia:global", LIMITE_GLOBAL_IA_MINUTO, 60)
    if not ok:
        return (jsonify({"ok": False, "erro": "Servidor de IA ocupado. Aguarde %d s." % espera,
                         "espera": espera}), 429), conexao
    return None, conexao


def _regravar_html_com_ia(pasta, resultado, analise):
    import comparativo_interativo
    cfg = {"identidade": identidade_atual(), "caminho_logo": caminho_logo_configurado() or ""}
    html = comparativo_interativo.montar(dict(resultado, analise_ia=analise), cfg)
    with open(os.path.join(pasta, "comparativo.html.tmp"), "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(os.path.join(pasta, "comparativo.html.tmp"),
               os.path.join(pasta, "comparativo.html"))


@app.route("/resultado/<execucao_id>/ia", methods=["GET", "POST"])
def gerar_analise_ia(execucao_id):
    """Analise completa (painel lateral). GET devolve a ja feita, sem gastar tokens."""
    execucao = _validar_execucao(execucao_id)
    pasta = pasta_resultado(execucao)
    if request.method == "GET":
        analise = _ler_json(os.path.join(pasta, "analise_ia.json"))
        return jsonify({"ok": bool(analise), "analise": analise})
    _exigir_json_mesma_origem()
    erro, conexao = _barreira_ia("analise_completa")
    if erro:
        return erro
    pasta, resultado, _ = _resultado_e_revisao(execucao)
    log = _logger_execucao(execucao_id)
    try:
        analise = analise_ia.analisar(resultado, CFG_WEB, execucao["departamento"], log,
                                      conexao=conexao)
    except analise_ia.ErroIA as falha:
        return jsonify({"ok": False, "erro": str(falha)}), 502
    registrar_uso_ia("analise", analise.get("uso"))
    gravar_json_atomico(os.path.join(pasta, "analise_ia.json"), analise)
    _regravar_html_com_ia(pasta, resultado, analise)
    execucao["ia_em"] = analise["gerado_em"]
    salvar_execucao(execucao)
    return jsonify({"ok": True, "ocorrencias": len(analise["ocorrencias"]), "analise": analise})


@app.route("/resultado/<execucao_id>/ia/resumo", methods=["GET", "POST"])
def resumo_ia(execucao_id):
    """Resumo rapido (quadro Resultado). Fica guardado: repetir nao gasta tokens."""
    execucao = _validar_execucao(execucao_id)
    caminho = os.path.join(pasta_resultado(execucao), "ia_resumo.json")
    salvo = _ler_json(caminho)
    if request.method == "GET":
        return jsonify({"ok": bool(salvo), "resumo": salvo})
    _exigir_json_mesma_origem()
    refazer = configuracao._bool((request.get_json(silent=True) or {}).get("refazer"))
    if salvo and not refazer:
        registrar_uso_ia("resumo", cache=True)
        return jsonify({"ok": True, "resumo": salvo, "cache": True})
    erro, conexao = _barreira_ia("resumo_rapido", "resumo:" + (request.remote_addr or ""), 10)
    if erro:
        return erro
    _, resultado, _ = _resultado_e_revisao(execucao)
    try:
        resumo = analise_ia.resumo_rapido(resultado, CFG_WEB, execucao["departamento"],
                                          _logger_execucao(execucao_id), conexao=conexao)
    except analise_ia.ErroIA as falha:
        return jsonify({"ok": False, "erro": str(falha)}), 502
    registrar_uso_ia("resumo", resumo.get("uso"))
    gravar_json_atomico(caminho, resumo)
    return jsonify({"ok": True, "resumo": resumo, "cache": False})


def sugestoes_chat(resultado):
    """Perguntas de exemplo, montadas a partir do proprio resultado."""
    sugestoes = ["Quais cláusulas o fornecedor alterou e qual o impacto para a contratante?"]
    criticas = sorted((o for o in resultado.get("ocorrencias", []) if o.get("critico")),
                      key=lambda o: -o.get("risco", 0))
    if criticas:
        clausula = re.sub(r"\s+", " ", criticas[0].get("clausula") or "")[:60]
        sugestoes.append("Por que a alteração na cláusula “%s” é crítica?" % clausula)
    if resultado.get("pendencias"):
        sugestoes.append("Quais campos ficaram sem preencher e o que isso implica?")
    if any(r.get("status") == "fora" for r in resultado.get("playbook") or []):
        sugestoes.append("O que está fora da política (playbook) e como negociar?")
    sugestoes += ["Sugira uma contraproposta para a alteração mais crítica.",
                  "O que significa foro de eleição?",
                  "Qual a diferença entre multa moratória e multa compensatória?"]
    return sugestoes[:6]


def _mensagens_publicas(historico, limite=60):
    return [{k: m.get(k) for k in ("papel", "texto", "em", "cache")}
            for m in (historico.get("mensagens") or [])[-limite:]]


def _perguntas_hoje(historico):
    hoje = datetime.now().strftime("%Y-%m-%d")
    return sum(1 for m in (historico.get("mensagens") or []) + (historico.get("arquivadas") or [])
               if m.get("papel") == "usuario" and str(m.get("em", "")).startswith(hoje)
               and not m.get("cache"))


@app.route("/resultado/<execucao_id>/ia/chat", methods=["GET", "POST"])
def chat_ia(execucao_id):
    """Assistente do contrato. Toda pergunta passa por ia_filtro antes da API."""
    execucao = _validar_execucao(execucao_id)
    pasta = pasta_resultado(execucao)
    caminho = os.path.join(pasta, "ia_chat.json")
    historico = _ler_json(caminho, {}) or {}
    cfg_ia = CFG_WEB.get("ia") or {}
    limite_dia = int(cfg_ia.get("chat_por_contrato_dia") or 30)
    if request.method == "GET":
        resultado = _ler_json(os.path.join(pasta, "resultado.json"), {}) or {}
        return jsonify({"ok": True, "mensagens": _mensagens_publicas(historico),
                        "sugestoes": sugestoes_chat(resultado),
                        "ativo": analise_ia.recurso_ativo(CFG_WEB, "chat", conexao_ia()),
                        "max_caracteres": int(cfg_ia.get("chat_max_caracteres") or 500),
                        "restantes_hoje": max(0, limite_dia - _perguntas_hoje(historico))})
    _exigir_json_mesma_origem()
    if request.content_length and request.content_length > 16 * 1024:
        return jsonify({"ok": False, "erro": "Pergunta grande demais."}), 413
    conexao = conexao_ia()
    if not analise_ia.recurso_ativo(CFG_WEB, "chat", conexao):
        return jsonify({"ok": False, "erro": "Assistente desligado. Veja Ajustes → IA."}), 403
    pergunta = str((request.get_json(silent=True) or {}).get("pergunta") or "")[:5000]
    _, resultado, _ = _resultado_e_revisao(execucao)
    log = _logger_execucao(execucao_id)

    avaliacao = ia_filtro.avaliar(pergunta, cfg_ia, ia_filtro.vocabulario_contrato(resultado))
    if not avaliacao["ok"]:
        registrar_uso_ia("chat", bloqueada=True)
        log.info("IA: pergunta bloqueada pelo filtro motivo=%s tamanho=%d",
                 avaliacao["codigo"], len(pergunta))
        return jsonify({"ok": False, "bloqueada": True, "codigo": avaliacao["codigo"],
                        "erro": avaliacao["mensagem"]})
    texto = avaliacao["texto"]
    agora = datetime.now().isoformat(timespec="seconds")
    chave = ia_filtro.chave_cache(texto)
    cache = historico.setdefault("cache", {})
    if cfg_ia.get("cache_respostas", True) and chave in cache:
        resposta = cache[chave]["texto"]
        with _trava:
            historico.setdefault("mensagens", []).extend([
                {"papel": "usuario", "texto": texto, "em": agora, "cache": True},
                {"papel": "ia", "texto": resposta, "em": agora, "cache": True}])
            historico["mensagens"] = historico["mensagens"][-200:]
            gravar_json_atomico(caminho, historico)
        registrar_uso_ia("chat", cache=True)
        return jsonify({"ok": True, "resposta": resposta, "cache": True,
                        "restantes_hoje": max(0, limite_dia - _perguntas_hoje(historico))})
    if _perguntas_hoje(historico) >= limite_dia:
        return jsonify({"ok": False, "erro": "Limite de %d perguntas por contrato hoje atingido."
                        % limite_dia}), 429
    erro, conexao = _barreira_ia("chat", "chat:" + (request.remote_addr or ""),
                                 int(cfg_ia.get("chat_por_minuto") or 6))
    if erro:
        return erro
    try:
        saida = analise_ia.responder_chat(resultado, texto, historico.get("mensagens"),
                                          CFG_WEB, execucao["departamento"], log,
                                          conexao=conexao)
    except analise_ia.ErroIA as falha:
        return jsonify({"ok": False, "erro": str(falha)}), 502
    registrar_uso_ia("chat", saida.get("uso"))
    with _trava:
        historico.setdefault("mensagens", []).extend([
            {"papel": "usuario", "texto": texto, "em": agora},
            {"papel": "ia", "texto": saida["texto"], "em": agora}])
        historico["mensagens"] = historico["mensagens"][-200:]
        if not saida["fora_do_escopo"]:
            cache[chave] = {"texto": saida["texto"], "em": agora}
            if len(cache) > 300:
                for antiga in sorted(cache, key=lambda k: cache[k]["em"])[:50]:
                    cache.pop(antiga, None)
        gravar_json_atomico(caminho, historico)
    return jsonify({"ok": True, "resposta": saida["texto"], "cache": False,
                    "fora_do_escopo": saida["fora_do_escopo"],
                    "restantes_hoje": max(0, limite_dia - _perguntas_hoje(historico))})


@app.route("/resultado/<execucao_id>/ia/chat/limpar", methods=["POST"])
def limpar_chat_ia(execucao_id):
    _exigir_json_mesma_origem()
    execucao = _validar_execucao(execucao_id)
    caminho = os.path.join(pasta_resultado(execucao), "ia_chat.json")
    historico = _ler_json(caminho, {}) or {}
    historico["arquivadas"] = (historico.get("arquivadas") or []) + (
        historico.get("mensagens") or [])
    historico["arquivadas"] = historico["arquivadas"][-400:]   # auditoria: nada se perde
    historico["mensagens"] = []
    gravar_json_atomico(caminho, historico)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- historico e painel

def _filtros_historico():
    dep = request.args.get("dep", "")
    if dep not in codigos_departamento() and dep != "*":
        dep = dep_da_requisicao()
    return {"departamento": "" if dep == "*" else dep,
            "fornecedor": (request.args.get("fornecedor") or "")[:80],
            "de": (request.args.get("de") or "")[:10], "ate": (request.args.get("ate") or "")[:10],
            "status": request.args.get("status") if request.args.get("status") in STATUS_REVISAO
            else "",
            "busca": (request.args.get("busca") or "")[:80]}, dep


@app.route("/historico")
def pagina_historico():
    filtros, dep = _filtros_historico()
    lista = recursos.filtrar_execucoes(EXECUCOES, **filtros)
    codigo = filtros["departamento"] or dep_da_requisicao()
    return render_template("historico.html", pagina="historico",
                           departamentos=CFG_WEB["departamentos"], departamento=codigo,
                           dep_filtro=dep, nome_dep=nome_departamento(codigo)
                           if filtros["departamento"] else "Todos os departamentos",
                           execucoes=lista, filtros=filtros, painel=recursos.painel(lista),
                           status_revisao=STATUS_REVISAO,
                           docusign_ativo=cfg_docusign(codigo).get("ativo", False))


@app.route("/painel")
def pagina_painel():
    """Indicadores e graficos (sairam do Historico para deixar a lista mais limpa)."""
    filtros, dep = _filtros_historico()
    lista = recursos.filtrar_execucoes(EXECUCOES, **filtros)
    codigo = filtros["departamento"] or dep_da_requisicao()
    return render_template("painel.html", pagina="painel",
                           departamentos=CFG_WEB["departamentos"], departamento=codigo,
                           dep_filtro=dep, nome_dep=nome_departamento(codigo)
                           if filtros["departamento"] else "todos os departamentos",
                           filtros=filtros, painel=recursos.painel(lista))


@app.route("/historico/csv")
def historico_csv():
    filtros, _ = _filtros_historico()
    corpo = recursos.csv_execucoes(recursos.filtrar_execucoes(EXECUCOES, **filtros))
    return Response(corpo, mimetype="text/csv", headers={
        "Content-Disposition": "attachment; filename=historico_%s.csv"
                               % datetime.now().strftime("%Y%m%d_%H%M")})


@app.route("/lote/<lote_id>")
def pagina_lote(lote_id):
    if not re.match(r"^[0-9a-f]{12}$", lote_id):
        abort(404)
    resumo = recursos.resumo_lote(EXECUCOES, lote_id)
    if not resumo["itens"]:
        abort(404)
    return render_template("lote.html", pagina="comparar",
                           departamentos=CFG_WEB["departamentos"],
                           departamento=resumo["departamento"],
                           nome_dep=nome_departamento(resumo["departamento"]), lote=resumo)


@app.route("/lote/<lote_id>/status")
def status_lote(lote_id):
    if not re.match(r"^[0-9a-f]{12}$", lote_id):
        abort(404)
    r = recursos.resumo_lote(EXECUCOES, lote_id)
    return jsonify({"concluidas": r["concluidas"], "pendentes": r["pendentes_fila"],
                    "erros": r["erros"], "total": len(r["itens"])})


@app.route("/lote/<lote_id>/xlsx")
def xlsx_lote(lote_id):
    if not re.match(r"^[0-9a-f]{12}$", lote_id):
        abort(404)
    resumo = recursos.resumo_lote(EXECUCOES, lote_id)
    if not resumo["itens"]:
        abort(404)
    buf = recursos.xlsx_lote(resumo, tokens_identidade()["relatorio_cabecalho"])
    return send_file(buf, as_attachment=True, download_name="lote_%s.xlsx" % lote_id,
                     mimetype=ARQUIVOS_SAIDA["xlsx"][1])


# ---------------------------------------------------------------- versoes de modelo

@app.route("/modelos/versoes/<codigo>/<arquivo>")
def api_versoes_modelo(codigo, arquivo):
    _, nome = caminho_modelo_seguro(codigo, arquivo)
    registro = ler_meta(codigo).get(nome, {})
    pasta = os.path.join(PASTA_MODELOS, codigo)
    versoes = [v for v in registro.get("versoes", [])
               if recursos.caminho_versao(pasta, nome, v["arquivo"])]
    return jsonify({"arquivo": nome, "versoes": versoes})


@app.route("/modelos/versoes/<codigo>/<arquivo>/baixar/<versao>")
def baixar_versao_modelo(codigo, arquivo, versao):
    _, nome = caminho_modelo_seguro(codigo, arquivo)
    caminho = recursos.caminho_versao(os.path.join(PASTA_MODELOS, codigo), nome, versao)
    if not caminho:
        abort(404)
    return send_file(caminho, as_attachment=True, download_name=versao)


@app.route("/modelos/versoes/<codigo>/<arquivo>/comparar", methods=["POST"])
def comparar_versao_modelo(codigo, arquivo):
    """Modelo x modelo: versao antiga (base) contra a atual."""
    _exigir_json_mesma_origem()
    atual, nome = caminho_modelo_seguro(codigo, arquivo)
    versao = str((request.get_json(silent=True) or {}).get("versao") or "")
    pasta_dep = os.path.join(PASTA_MODELOS, codigo)
    antigo = recursos.caminho_versao(pasta_dep, nome, versao)
    if not antigo:
        abort(404)
    cfg = cfg_motor(codigo, "")
    cfg.update({"fornecedor_forcado": "versão do modelo", "playbook": [],
                "biblioteca_aprovada": {}})
    resultado = motor.analisar(antigo, atual, cfg)
    resultado["meta"]["fornecedor"] = "Modelo %s" % nome
    carimbo = versao[:15]
    destino = os.path.join(recursos.pasta_versoes(pasta_dep, nome), "_comparacoes", carimbo)
    os.makedirs(destino, exist_ok=True)
    import comparativo_interativo
    gravar_json_atomico(os.path.join(destino, "resultado.json"), resultado)
    with open(os.path.join(destino, "comparativo.html.tmp"), "w", encoding="utf-8") as f:
        f.write(comparativo_interativo.montar(resultado, cfg))
    os.replace(os.path.join(destino, "comparativo.html.tmp"),
               os.path.join(destino, "comparativo.html"))
    return jsonify({"ok": True, "total": resultado["resumo"]["TOTAL"]["total"],
                    "url": url_for("ver_comparacao_versao", codigo=codigo, arquivo=nome,
                                   carimbo=carimbo)})


@app.route("/modelos/versoes/<codigo>/<arquivo>/comparacao/<carimbo>")
def ver_comparacao_versao(codigo, arquivo, carimbo):
    _, nome = caminho_modelo_seguro(codigo, arquivo)
    if not re.match(r"^\d{8}_\d{6}$", carimbo):
        abort(404)
    caminho = os.path.join(recursos.pasta_versoes(os.path.join(PASTA_MODELOS, codigo), nome),
                           "_comparacoes", carimbo, "comparativo.html")
    if not os.path.isfile(caminho):
        abort(404)
    with open(caminho, encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- API v1 (docs/api.md)

def _api_autorizada():
    token = (CFG_WEB.get("api_token") or "").strip()
    if not token:
        return True
    enviado = request.headers.get("Authorization", "")
    import hmac
    return hmac.compare_digest(enviado, "Bearer " + token)


@app.before_request
def _proteger_api():
    if not request.path.startswith("/api/v1/"):
        return None
    if not _api_autorizada():
        return jsonify({"erro": "Não autorizado."}), 401
    # sem token configurado, escrita so com cabecalho (bloqueia formulario de outro site)
    if (request.method not in ("GET", "HEAD") and not (CFG_WEB.get("api_token") or "").strip()
            and request.headers.get("X-Requested-With") is None):
        return jsonify({"erro": "Envie o cabeçalho X-Requested-With ou configure "
                                "api_token."}), 403
    return None


CAMPOS_API = ("id", "departamento", "arquivo", "fornecedor", "cnpj", "modelo", "status",
              "status_revisao", "responsavel", "criado_em_iso", "concluido_em_iso", "rodada",
              "risco_geral", "selo_risco", "preenchidos", "alterados", "incluidos",
              "removidos", "movidos", "pendentes", "criticos", "total",
              "devolucao_incompleta", "fora_da_politica", "versao_motor", "erro", "lote",
              "hash_entrada", "revisao")


def _execucao_api(e):
    dados = {k: e.get(k) for k in CAMPOS_API}
    if e.get("status") == "concluido":
        dados["links"] = {
            "resultado": url_for("api_v1_resultado", execucao_id=e["id"]),
            "arquivos": {f: url_for("api_v1_arquivo", execucao_id=e["id"], formato=f)
                         for f in ("json", "html", "pdf", "xlsx", "txt")},
            "revisao": url_for("api_revisao", execucao_id=e["id"])}
    return dados


@app.route("/api/v1/departamentos")
def api_v1_departamentos():
    return jsonify([{k: d.get(k) for k in ("codigo", "nome", "ativo", "excluido")}
                    for d in CFG_WEB["departamentos"]])


@app.route("/api/v1/modelos/<codigo>")
def api_v1_modelos(codigo):
    if codigo not in codigos_departamento():
        abort(404)
    return jsonify(modelos_do_departamento(codigo))


@app.route("/api/v1/comparacoes", methods=["GET", "POST"])
def api_v1_comparacoes():
    if request.method == "GET":
        filtros = {"departamento": request.args.get("departamento", ""),
                   "fornecedor": request.args.get("fornecedor", ""),
                   "de": request.args.get("de", ""), "ate": request.args.get("ate", ""),
                   "status": request.args.get("status_revisao", "")}
        try:
            limite = max(1, min(500, int(request.args.get("limite", 100))))
        except ValueError:
            limite = 100
        lista = recursos.filtrar_execucoes(EXECUCOES, **filtros)[:limite]
        return jsonify([_execucao_api(e) for e in lista])
    codigo = request.form.get("departamento", "")
    modelo = secure_filename(request.form.get("modelo", ""))
    if codigo not in codigos_departamento(so_ativos=True):
        return jsonify({"erro": "Departamento inválido."}), 400
    caminho_modelo = os.path.join(PASTA_MODELOS, codigo, modelo)
    if not modelo or not os.path.isfile(caminho_modelo):
        return jsonify({"erro": "Modelo inválido."}), 400
    arquivos = [a for a in request.files.getlist("arquivo") + request.files.getlist("arquivos")
                if a and a.filename]
    if not arquivos:
        return jsonify({"erro": "Envie o arquivo em 'arquivo'."}), 400
    resposta = enfileirar_arquivos(codigo, caminho_modelo, modelo, arquivos,
                                   (request.form.get("fornecedor") or "").strip()[:120],
                                   request.form.get("forcar") == "1")
    if not resposta["ids"] and not resposta["duplicados"]:
        return jsonify({"erro": "Extensão não aceita."}), 400
    return jsonify(resposta), 202


@app.route("/api/v1/comparacoes/<execucao_id>")
def api_v1_comparacao(execucao_id):
    return jsonify(_execucao_api(_validar_execucao(execucao_id, exigir_concluido=False)))


@app.route("/api/v1/comparacoes/<execucao_id>/resultado")
def api_v1_resultado(execucao_id):
    execucao = _validar_execucao(execucao_id)
    caminho = os.path.join(pasta_resultado(execucao), "resultado.json")
    if not os.path.isfile(caminho):
        abort(404)
    return send_file(caminho, mimetype="application/json")


@app.route("/api/v1/comparacoes/<execucao_id>/arquivos/<formato>")
def api_v1_arquivo(execucao_id, formato):
    return baixar(execucao_id, formato)


@app.errorhandler(413)
def arquivo_grande(_erro):
    return jsonify({"erro": "Arquivo acima de %d MB."
                    % CFG_WEB["tamanho_maximo_mb"]}), 413


# ---------------------------------------------------------------- inicializacao

_SERVICOS_INICIADOS = False


def migrar_historico():
    """Completa execucoes antigas com os campos novos (idempotente)."""
    for execucao in EXECUCOES.values():
        execucao.setdefault("status_revisao", "pendente")
        execucao.setdefault("versao_motor", "1.1" if not execucao.get("hash_entrada")
                            else motor.VERSAO)
        if execucao.get("status") == "concluido" and "clausulas_alteradas" not in execucao:
            resultado = _ler_json(os.path.join(pasta_resultado(execucao), "resultado.json"))
            execucao["clausulas_alteradas"] = recursos.clausulas_alteradas(resultado) \
                if resultado else {}
    recursos.numerar_rodadas(EXECUCOES)


def iniciar_servicos():
    """Pastas, historico e worker da fila. Idempotente; usado por iniciar(),
    pelos testes e pelo wsgi.py (Gunicorn)."""
    global _SERVICOS_INICIADOS
    if _SERVICOS_INICIADOS:
        return
    _SERVICOS_INICIADOS = True
    preparar_pastas()
    try:
        analise_ia.carregar_conexao(criar=True)        # cria ia_config/config.json modelo
    except OSError:
        pass
    EXECUCOES.update(carregar_historico())
    for execucao in EXECUCOES.values():
        if execucao.get("status") in ("na_fila", "processando"):
            execucao["status"] = "erro"
            execucao["erro"] = "Interrompido ao reiniciar o servidor."
    migrar_historico()
    gravar_historico()
    threading.Thread(target=_worker, daemon=True).start()


def iniciar():
    iniciar_servicos()

    url = "http://%s:%d" % (CFG_WEB["host"], CFG_WEB["porta"])
    print("=" * 62)
    print("  %s - versao web" % identidade_atual()["nome_produto"])
    print("  Abra no navegador: %s" % url)
    print("  Para encerrar: feche esta janela ou pressione Ctrl+C")
    print("=" * 62)

    if CFG_WEB.get("abrir_navegador", True):
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(host=CFG_WEB["host"], port=CFG_WEB["porta"], debug=False,
            use_reloader=False, threaded=True)


if __name__ == "__main__":
    iniciar()
