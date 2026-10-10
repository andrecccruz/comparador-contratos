# -*- coding: utf-8 -*-
"""
configuracao.py
Esquema, padroes, validacao e gravacao do config_web.json editado pela tela Ajustes.

- Cada secao tem um validador que recebe o que veio do navegador e devolve
  (valor_limpo, erros). Nada do navegador e gravado sem passar por aqui.
- Gravacao atomica (.tmp + os.replace) com backup da versao anterior em
  <dados>/config_backups/ (nada e apagado).
- Segredos (credenciais DocuSign, chave de IA) nunca voltam ao navegador: a
  tela so recebe "configurado"; campo em branco mantem o valor salvo.
- A conexao de IA (provedor, endpoint, chave) fica em ia_config/config.json
  (validar_ia_conexao); aqui so o comportamento (liga/desliga, limites, filtro).
"""

import os
import re
import json
import copy
import string
from datetime import datetime

import identidade as identidade_mod
import comparar_contratos as motor

TIPOS = list(motor.TIPOS)
EXTENSOES_POSSIVEIS = [".docx", ".pdf", ".txt"]
FORMATOS_DATA = {"%d.%m.%Y": "31.12.2026", "%Y-%m-%d": "2026-12-31", "%d-%m-%Y": "31-12-2026"}
FORMATOS_HORA = {"%Hh%M": "14h30", "%H-%M": "14-30", "%H%M": "1430"}
VISOES = {"inline": "Lista", "lado_a_lado": "Lado a lado", "documento": "Documento"}
ICONES = ["documento", "balanca", "capacete", "megafone", "monitor", "pessoas", "predio",
          "carrinho", "engrenagem"]
VARIAVEIS_MENSAGEM = ("fornecedor", "departamento", "arquivo", "data")
SEGREDOS_DOCUSIGN = ("integration_key", "user_id", "account_id")
FUNCOES_IA = {"resumo": "Resumo executivo", "explicacao": "Explicação do risco das críticas",
              "sugestao": "Sugestão de contraproposta por cláusula",
              "playbook": "Checagem semântica contra o playbook"}
RE_CODIGO = re.compile(r"^[A-Z0-9]{2,6}$")
RE_BASE_PATH = re.compile(r"^https://[a-z0-9.\-]+\.docusign\.(net|com)/restapi$")
AUTH_SERVERS = ("account-d.docusign.com", "account.docusign.com")
RE_GUID = re.compile(r"^[0-9A-Za-z\-]{8,64}$")

CHAVES_ANALISE = ("termos_criticos", "nomes_ignorar", "regex_placeholder",
                  "similaridade_minima_par", "limiares_risco", "categorias_ocultas",
                  "ignorar_diferencas_apenas_formatacao", "cnpj_contratante")
CHAVES_SAIDAS = ("gerar_html", "gerar_pdf", "gerar_xlsx", "gerar_txt", "formato_data_pasta",
                 "incluir_hora_na_pasta", "formato_hora_pasta", "incluir_texto_integral",
                 "visao_padrao")

DOCUSIGN_PADRAO_DEPTO = {
    "ativo": False, "integration_key": "", "user_id": "", "account_id": "",
    "caminho_chave_privada": "", "base_path": "https://demo.docusign.net/restapi",
    "auth_server": "account-d.docusign.com",
    "assunto_padrao": "Contrato para assinatura — {fornecedor}",
    "mensagem_padrao": "Segue o contrato para assinatura eletrônica.",
}
RECURSOS_IA = {"resumo_rapido": "Resumo com IA no quadro Resultado (bullets curtos)",
               "analise_completa": "Análise completa no painel lateral do resultado",
               "chat": "Assistente (chat) sobre o contrato"}
RIGORES_FILTRO = {"estrito": "Estrito — a pergunta precisa citar o contrato ou um termo jurídico",
                  "moderado": "Moderado — só barra injeção, conta e assuntos claramente fora"}
IA_PADRAO = {"ativo": False, "anonimizar": True, "funcoes": list(FUNCOES_IA),
             "resumo_rapido": True, "analise_completa": True, "chat": True,
             "max_tokens_resumo": 500, "max_tokens_analise": 3000, "max_tokens_chat": 700,
             "chat_max_caracteres": 500, "chat_por_minuto": 6, "chat_por_contrato_dia": 30,
             "limite_tokens_dia": 150000, "historico_turnos": 3, "chat_limite_contexto": 6000,
             "filtro_rigor": "estrito", "cache_respostas": True,
             "termos_permitidos": [], "termos_bloqueados": []}
# faixas aceitas para os numeros da aba IA: (minimo, maximo, rotulo)
LIMITES_IA = {"max_tokens_resumo": (100, 4000, "Tokens do resumo"),
              "max_tokens_analise": (500, 16000, "Tokens da análise"),
              "max_tokens_chat": (100, 8000, "Tokens por resposta do chat"),
              "chat_max_caracteres": (50, 2000, "Tamanho máximo da pergunta"),
              "chat_por_minuto": (1, 60, "Perguntas por minuto"),
              "chat_por_contrato_dia": (1, 500, "Perguntas por contrato por dia"),
              "limite_tokens_dia": (0, 10000000, "Tokens por dia"),
              "historico_turnos": (0, 10, "Turnos de histórico"),
              "chat_limite_contexto": (1000, 30000, "Contexto do chat (caracteres)")}


def padrao_analise():
    return {k: copy.deepcopy(motor.CONFIG_PADRAO[k]) for k in CHAVES_ANALISE}


def padrao_saidas():
    return {k: copy.deepcopy(motor.CONFIG_PADRAO[k]) for k in CHAVES_SAIDAS}


def completar(cfg):
    """Garante todas as secoes novas com padrao (migracao idempotente)."""
    cfg.setdefault("identidade", dict(identidade_mod.IDENTIDADE_PADRAO))
    for chave, valor in identidade_mod.IDENTIDADE_PADRAO.items():
        cfg["identidade"].setdefault(chave, valor)
    if not isinstance(cfg.get("identidade_perfis"), dict):
        cfg["identidade_perfis"] = {}
    if not cfg.get("perfil_identidade"):
        cfg["perfil_identidade"] = "padrao"
    for d in cfg.get("departamentos", []):
        d.setdefault("ativo", True)
        d.setdefault("cor", "")
        d.setdefault("icone", "documento")
        d.setdefault("modelo_padrao", "")
        d.setdefault("excluido", False)
        if d["excluido"]:
            d["ativo"] = False
    analise = cfg.setdefault("analise", {})
    analise.setdefault("global", {})
    analise.setdefault("departamentos", {})
    cfg.setdefault("playbook", [])
    saidas = cfg.setdefault("saidas", {})
    for chave, valor in padrao_saidas().items():
        saidas.setdefault(chave, valor)
    ia = cfg.setdefault("ia", {})
    for chave, valor in IA_PADRAO.items():
        ia.setdefault(chave, copy.deepcopy(valor))
    # conexao (modelo/chave) fica em ia_config/config.json
    for antiga in ("modelo", "chave_api"):
        ia.pop(antiga, None)
    cfg.pop("ia_externa_permitida", None)
    return cfg


# ----------------------------------------------------------------- utilitarios

def _bool(valor):
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "on", "sim", "yes")


def _int(valor, minimo, maximo, erros, campo, rotulo):
    try:
        n = int(float(str(valor).replace(",", ".")))
    except (TypeError, ValueError):
        erros[campo] = "%s: informe um número." % rotulo
        return None
    if not minimo <= n <= maximo:
        erros[campo] = "%s: entre %d e %d." % (rotulo, minimo, maximo)
        return None
    return n


def _float(valor, minimo, maximo, erros, campo, rotulo):
    try:
        n = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        erros[campo] = "%s: informe um número." % rotulo
        return None
    if not minimo <= n <= maximo:
        erros[campo] = "%s: entre %s e %s." % (rotulo, minimo, maximo)
        return None
    return n


def _texto(valor, maximo, erros, campo, rotulo, obrigatorio=False):
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(valor or "")).strip()
    if obrigatorio and not t:
        erros[campo] = "%s é obrigatório." % rotulo
    elif len(t) > maximo:
        erros[campo] = "%s: no máximo %d caracteres." % (rotulo, maximo)
    return t[:maximo]


def _linhas(valor):
    if isinstance(valor, list):
        itens = valor
    else:
        itens = str(valor or "").splitlines()
    return [str(i).strip() for i in itens if str(i).strip()]


def _lista(valor):
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


def validar_mensagem(texto, erros, campo, rotulo, maximo):
    t = _texto(texto, maximo, erros, campo, rotulo)
    try:
        nomes = [c[1] for c in string.Formatter().parse(t) if c[1] is not None]
    except ValueError:
        erros[campo] = "%s: chaves { } desbalanceadas." % rotulo
        return t
    invalidas = [n for n in nomes if n not in VARIAVEIS_MENSAGEM]
    if invalidas:
        erros[campo] = "%s: variável desconhecida {%s}. Use %s." % (
            rotulo, invalidas[0], ", ".join("{%s}" % v for v in VARIAVEIS_MENSAGEM))
    return t


def formatar_mensagem(modelo, **valores):
    seguros = {k: str(valores.get(k, "")) for k in VARIAVEIS_MENSAGEM}
    try:
        return modelo.format(**seguros)
    except (KeyError, ValueError, IndexError):
        return modelo


# ----------------------------------------------------------------- validadores

def validar_identidade(dados, atual):
    erros = {}
    novo = dict(atual.get("identidade") or {})
    novo["nome_produto"] = _texto(dados.get("nome_produto"), 80, erros, "nome_produto",
                                  "Nome do produto", True)
    novo["nome_curto"] = _texto(dados.get("nome_curto"), 30, erros, "nome_curto",
                                "Nome curto", True)
    novo["organizacao_contratante"] = _texto(dados.get("organizacao_contratante"), 80, erros,
                                             "organizacao_contratante",
                                             "Organização contratante", True)
    cor = str(dados.get("cor_primaria") or "").strip()
    if not identidade_mod.cor_valida(cor):
        erros["cor_primaria"] = "Cor inválida: use o formato #RRGGBB."
    else:
        novo["cor_primaria"] = identidade_mod.normalizar_cor(cor)
    novo["rodape_relatorio"] = _texto(dados.get("rodape_relatorio"), 300, erros,
                                      "rodape_relatorio", "Rodapé")
    if _bool(dados.get("remover_logo")):
        novo["logo"] = ""
    perfis = copy.deepcopy(atual.get("identidade_perfis") or {})
    perfis[atual.get("perfil_identidade") or "padrao"] = dict(novo)
    return {"identidade": novo, "identidade_perfis": perfis}, erros


def validar_departamentos(dados, atual, modelos_por_dep=None, com_dados=None,
                          permitir_remocao=True):
    """Lista enviada pela tela = departamentos que ficam visiveis.

    Departamento que sumiu da lista:
      - sem historico e sem modelos -> removido de vez (com credenciais e regras);
      - com historico ou modelos    -> arquivado ("excluido": true), some dos seletores,
        mas o historico continua acessivel e pode ser restaurado depois.
    Na importacao (permitir_remocao=False) nada e removido de vez: so arquivado.
    """
    erros = {}
    lista = dados.get("departamentos")
    if not isinstance(lista, list) or not lista:
        return None, {"departamentos": "Informe ao menos um departamento."}
    existentes = {d["codigo"]: d for d in atual.get("departamentos", [])}
    com_dados = set(com_dados or ())
    vistos, limpos = set(), []
    for i, d in enumerate(lista):
        if not isinstance(d, dict):
            continue
        codigo = str(d.get("codigo") or "").strip().upper()
        campo = "departamentos.%d" % i
        if not RE_CODIGO.match(codigo):
            erros[campo + ".codigo"] = "Código com 2 a 6 letras maiúsculas ou números."
            continue
        if codigo in vistos:
            erros[campo + ".codigo"] = "Código %s repetido." % codigo
            continue
        vistos.add(codigo)
        cor = str(d.get("cor") or "").strip()
        if cor and not identidade_mod.cor_valida(cor):
            erros[campo + ".cor"] = "Cor inválida."
        icone = d.get("icone") if d.get("icone") in ICONES else "documento"
        modelo = os.path.basename(str(d.get("modelo_padrao") or "").strip())
        if modelo and modelos_por_dep is not None and modelo not in modelos_por_dep.get(codigo, []):
            erros[campo + ".modelo_padrao"] = "Modelo não encontrado em %s." % codigo
        limpos.append({
            "codigo": codigo,
            "nome": _texto(d.get("nome"), 80, erros, campo + ".nome", "Nome", True),
            "ativo": _bool(d.get("ativo", True)),
            "cor": identidade_mod.normalizar_cor(cor) if cor and not erros.get(campo + ".cor")
            else "",
            "icone": icone,
            "modelo_padrao": modelo,
            "excluido": False,
        })
    if not any(d["ativo"] for d in limpos):
        erros["departamentos"] = "Mantenha ao menos um departamento ativo."

    arquivados, removidos = [], []
    for codigo, d in existentes.items():
        if codigo in vistos:
            continue
        if d.get("excluido") or codigo in com_dados or not permitir_remocao:
            arquivados.append(dict(d, ativo=False, excluido=True))
        else:
            removidos.append(codigo)

    parte = {"departamentos": limpos + arquivados}
    if removidos:
        # limpa o que so existia por causa do departamento removido
        parte["docusign"] = {c: v for c, v in (atual.get("docusign") or {}).items()
                             if c not in removidos}
        analise = copy.deepcopy(atual.get("analise") or {"global": {}, "departamentos": {}})
        for c in removidos:
            (analise.get("departamentos") or {}).pop(c, None)
        parte["analise"] = analise
        playbook = copy.deepcopy(atual.get("playbook") or [])
        for regra in playbook:
            regra["departamentos"] = [c for c in regra.get("departamentos") or []
                                      if c not in removidos]
        parte["playbook"] = playbook
    return parte, erros


def validar_regras(valores, erros, prefixo=""):
    """Campos de analise; so valida as chaves presentes em `valores`."""
    limpo = {}
    if "termos_criticos" in valores:
        termos = {}
        for n, linha in enumerate(_linhas(valores["termos_criticos"]), 1):
            if "=" in linha:
                termo, peso = linha.rsplit("=", 1)
            else:
                termo, peso = linha, "10"
            termo = termo.strip().lower()
            if not 2 <= len(termo) <= 60:
                erros[prefixo + "termos_criticos"] = "Linha %d: termo com 2 a 60 caracteres." % n
                continue
            try:
                p = int(peso.strip())
                if not 0 <= p <= 50:
                    raise ValueError
            except ValueError:
                erros[prefixo + "termos_criticos"] = "Linha %d: peso de 0 a 50." % n
                continue
            termos[termo] = p
        if len(termos) > 300:
            erros[prefixo + "termos_criticos"] = "No máximo 300 termos."
        limpo["termos_criticos"] = termos
    if "nomes_ignorar" in valores:
        nomes = [n[:80] for n in _linhas(valores["nomes_ignorar"])][:100]
        limpo["nomes_ignorar"] = nomes
    if "regex_placeholder" in valores:
        padroes = []
        for n, linha in enumerate(_linhas(valores["regex_placeholder"]), 1):
            if len(linha) > 200:
                erros[prefixo + "regex_placeholder"] = "Linha %d: expressão longa demais." % n
                continue
            try:
                compilado = re.compile(linha)
            except re.error as erro:
                erros[prefixo + "regex_placeholder"] = "Linha %d: expressão inválida (%s)." % (
                    n, erro)
                continue
            if compilado.match(""):
                erros[prefixo + "regex_placeholder"] = ("Linha %d: a expressão aceita texto "
                                                        "vazio." % n)
                continue
            padroes.append(linha)
        if not padroes and prefixo + "regex_placeholder" not in erros:
            erros[prefixo + "regex_placeholder"] = "Informe ao menos uma expressão."
        limpo["regex_placeholder"] = padroes
    if "similaridade_minima_par" in valores:
        v = _float(valores["similaridade_minima_par"], 0.3, 0.95, erros,
                   prefixo + "similaridade_minima_par", "Similaridade mínima")
        if v is not None:
            limpo["similaridade_minima_par"] = round(v, 3)
    if "limiares_risco" in valores or "limiar_medio" in valores:
        bruto = valores.get("limiares_risco") or {}
        medio = _int(bruto.get("medio", valores.get("limiar_medio")), 1, 99, erros,
                     prefixo + "limiar_medio", "Limiar médio")
        alto = _int(bruto.get("alto", valores.get("limiar_alto")), 2, 100, erros,
                    prefixo + "limiar_alto", "Limiar alto")
        if medio is not None and alto is not None:
            if medio >= alto:
                erros[prefixo + "limiar_alto"] = "O limiar alto deve ser maior que o médio."
            else:
                limpo["limiares_risco"] = {"medio": medio, "alto": alto}
    if "categorias_ocultas" in valores:
        limpo["categorias_ocultas"] = [t for t in _lista(valores["categorias_ocultas"])
                                       if t in TIPOS]
    if "ignorar_diferencas_apenas_formatacao" in valores:
        limpo["ignorar_diferencas_apenas_formatacao"] = _bool(
            valores["ignorar_diferencas_apenas_formatacao"])
    if "cnpj_contratante" in valores:
        cnpj = str(valores["cnpj_contratante"] or "").strip()
        if cnpj and not motor.validar_cnpj(cnpj):
            erros[prefixo + "cnpj_contratante"] = "CNPJ inválido."
        limpo["cnpj_contratante"] = cnpj
    return limpo


def validar_analise(dados, atual):
    erros = {}
    escopo = str(dados.get("escopo") or "global")
    codigos = [d["codigo"] for d in atual.get("departamentos", [])]
    if escopo != "global" and escopo not in codigos:
        return None, {"escopo": "Departamento desconhecido."}
    herdar = set(_lista(dados.get("herdar"))) if escopo != "global" else set()
    valores = {k: v for k, v in (dados.get("valores") or {}).items()}
    if "limiar_medio" in valores or "limiar_alto" in valores:
        valores["limiares_risco"] = {"medio": valores.pop("limiar_medio", None),
                                     "alto": valores.pop("limiar_alto", None)}
    if escopo == "global":
        for chave in CHAVES_ANALISE:
            if chave not in valores and chave in ("categorias_ocultas",):
                valores[chave] = []
            if chave == "ignorar_diferencas_apenas_formatacao" and chave not in valores:
                valores[chave] = False
    else:
        valores = {k: v for k, v in valores.items() if k not in herdar}
        if "categorias_ocultas" not in herdar and "categorias_ocultas" not in valores:
            valores["categorias_ocultas"] = []
        if ("ignorar_diferencas_apenas_formatacao" not in herdar
                and "ignorar_diferencas_apenas_formatacao" not in valores):
            valores["ignorar_diferencas_apenas_formatacao"] = False
    limpo = validar_regras(valores, erros)
    analise = copy.deepcopy(atual.get("analise") or {"global": {}, "departamentos": {}})
    if escopo == "global":
        analise["global"] = limpo
    else:
        analise.setdefault("departamentos", {})[escopo] = limpo
    return {"analise": analise}, erros


def validar_playbook(dados, atual):
    erros = {}
    regras = dados.get("playbook")
    if not isinstance(regras, list):
        return None, {"playbook": "Formato inválido."}
    if len(regras) > 100:
        return None, {"playbook": "No máximo 100 regras."}
    codigos = {d["codigo"] for d in atual.get("departamentos", [])}
    limpas = []
    for i, r in enumerate(regras):
        if not isinstance(r, dict):
            continue
        campo = r.get("campo")
        base = "playbook.%d" % i
        if campo not in motor.CAMPOS_PLAYBOOK:
            erros[base + ".campo"] = "Campo desconhecido."
            continue
        op = r.get("operador")
        if op not in motor.OPERADORES_PLAYBOOK:
            erros[base + ".operador"] = "Operador inválido."
            continue
        if campo == "foro_cidade":
            valor = _texto(r.get("valor"), 80, erros, base + ".valor", "Valor", True)
            if op not in ("=", "!="):
                erros[base + ".operador"] = "Para cidade use = ou !=."
        else:
            valor = _float(r.get("valor"), -1e12, 1e12, erros, base + ".valor", "Valor")
            if valor is not None and valor.is_integer():
                valor = int(valor)
        deps = [c for c in _lista(r.get("departamentos")) if c in codigos]
        rotulo = _texto(r.get("rotulo"), 80, erros, base + ".rotulo", "Rótulo")
        if not rotulo and valor is not None:
            rotulo = "%s %s %s" % (motor.CAMPOS_PLAYBOOK[campo], op, valor)
        rid = re.sub(r"[^a-z0-9_\-]", "", str(r.get("id") or "").lower())[:40] or "r%d" % (i + 1)
        limpas.append({"id": rid, "rotulo": rotulo, "campo": campo, "operador": op,
                       "valor": valor, "departamentos": deps, "ativo": _bool(r.get("ativo",
                                                                                     True))})
    ids = [r["id"] for r in limpas]
    for i, r in enumerate(limpas):
        if ids.count(r["id"]) > 1:
            r["id"] = "%s_%d" % (r["id"], i + 1)
    return {"playbook": limpas}, erros


def validar_saidas(dados, atual):
    erros = {}
    s = {}
    for chave in ("gerar_html", "gerar_pdf", "gerar_xlsx", "gerar_txt", "incluir_hora_na_pasta",
                  "incluir_texto_integral"):
        s[chave] = _bool(dados.get(chave, False))
    if not (s["gerar_html"] or s["gerar_pdf"]):
        erros["gerar_html"] = "Gere ao menos o HTML ou o PDF."
    s["formato_data_pasta"] = dados.get("formato_data_pasta") if dados.get(
        "formato_data_pasta") in FORMATOS_DATA else "%d.%m.%Y"
    s["formato_hora_pasta"] = dados.get("formato_hora_pasta") if dados.get(
        "formato_hora_pasta") in FORMATOS_HORA else "%Hh%M"
    s["visao_padrao"] = dados.get("visao_padrao") if dados.get("visao_padrao") in VISOES \
        else "inline"
    return {"saidas": s}, erros


def validar_armazenamento(dados, atual):
    erros = {}
    novo = {}
    v = _int(dados.get("retencao_dias", 0), 0, 3650, erros, "retencao_dias", "Retenção")
    if v is not None:
        novo["retencao_dias"] = v
    v = _int(dados.get("tamanho_maximo_mb", 40), 1, 500, erros, "tamanho_maximo_mb",
             "Tamanho máximo")
    if v is not None:
        novo["tamanho_maximo_mb"] = v
    ext = [e for e in _lista(dados.get("extensoes_aceitas")) if e in EXTENSOES_POSSIVEIS]
    if not ext:
        erros["extensoes_aceitas"] = "Aceite ao menos uma extensão."
    novo["extensoes_aceitas"] = ext
    return novo, erros


def validar_assinatura(dados, atual):
    erros = {}
    codigo = dados.get("departamento")
    if codigo not in [d["codigo"] for d in atual.get("departamentos", [])]:
        return None, {"departamento": "Departamento desconhecido."}
    todos = copy.deepcopy(atual.get("docusign") or {})
    cfg = dict(DOCUSIGN_PADRAO_DEPTO)
    cfg.update(todos.get(codigo) or {})
    if _bool(dados.get("limpar_credenciais")):
        for chave in SEGREDOS_DOCUSIGN + ("caminho_chave_privada",):
            cfg[chave] = ""
    for chave in SEGREDOS_DOCUSIGN:
        valor = str(dados.get(chave) or "").strip()
        if valor:                                  # em branco = mantem o salvo
            if not RE_GUID.match(valor):
                erros[chave] = "Formato inválido (letras, números e hífen)."
            else:
                cfg[chave] = valor
    base = str(dados.get("base_path") or cfg["base_path"]).strip()
    if not RE_BASE_PATH.match(base):
        erros["base_path"] = "Endereço da API DocuSign inválido."
    else:
        cfg["base_path"] = base
    auth = str(dados.get("auth_server") or cfg["auth_server"]).strip()
    if auth not in AUTH_SERVERS:
        erros["auth_server"] = "Servidor de autenticação inválido."
    else:
        cfg["auth_server"] = auth
    cfg["assunto_padrao"] = validar_mensagem(dados.get("assunto_padrao",
                                                       cfg["assunto_padrao"]), erros,
                                             "assunto_padrao", "Assunto", 100)
    cfg["mensagem_padrao"] = validar_mensagem(dados.get("mensagem_padrao",
                                                        cfg["mensagem_padrao"]), erros,
                                              "mensagem_padrao", "Mensagem", 1000)
    cfg["ativo"] = _bool(dados.get("ativo"))
    faltando = [c for c in SEGREDOS_DOCUSIGN + ("caminho_chave_privada",) if not cfg.get(c)]
    if cfg["ativo"] and faltando:
        erros["ativo"] = ("Para ativar, configure: %s." % ", ".join(
            "chave privada" if c == "caminho_chave_privada" else c for c in faltando))
    todos[codigo] = cfg
    return {"docusign": todos}, erros


def validar_ia(dados, atual, pendencias_conexao=None):
    """Comportamento da IA (config_web.json). A conexao fica em ia_config/config.json.
    pendencias_conexao: o que falta para ligar (liberacao da TI, chave...)."""
    erros = {}
    ia = copy.deepcopy(IA_PADRAO)
    ia.update(atual.get("ia") or {})
    for antiga in ("modelo", "chave_api"):
        ia.pop(antiga, None)
    ia["ativo"] = _bool(dados.get("ativo", ia["ativo"]))
    if "anonimizar" in dados or "funcoes" in dados:
        ia["anonimizar"] = _bool(dados.get("anonimizar"))
        ia["funcoes"] = [f for f in _lista(dados.get("funcoes")) if f in FUNCOES_IA]
        for recurso in RECURSOS_IA:
            ia[recurso] = _bool(dados.get(recurso))
    for chave, (minimo, maximo, rotulo) in LIMITES_IA.items():
        if chave in dados:
            v = _int(dados.get(chave), minimo, maximo, erros, chave, rotulo)
            if v is not None:
                ia[chave] = v
    if "filtro_rigor" in dados:
        ia["filtro_rigor"] = dados.get("filtro_rigor") if dados.get("filtro_rigor") in \
            RIGORES_FILTRO else "estrito"
    if "cache_respostas" in dados or "termos_permitidos" in dados:
        ia["cache_respostas"] = _bool(dados.get("cache_respostas"))
    for chave in ("termos_permitidos", "termos_bloqueados"):
        if chave in dados:
            termos = [t[:60] for t in _linhas(dados.get(chave))][:200]
            ia[chave] = termos
    if ia["ativo"] and pendencias_conexao:
        erros["ativo"] = "Para ligar a IA falta: %s." % "; ".join(pendencias_conexao)
    return {"ia": ia}, erros


RE_ENDPOINT_HTTPS = re.compile(r"^https://[A-Za-z0-9.\-]+(:\d{2,5})?(/[\w.\-/]*)?$")


def validar_ia_conexao(dados, conexao):
    """Campos editaveis de ia_config/config.json (a liberacao da TI nao e editavel aqui).
    Devolve (nova_conexao, erros). Chave em branco = mantem a salva."""
    import analise_ia
    erros = {}
    nova = copy.deepcopy(conexao)
    provedor = dados.get("provedor") or nova.get("provedor")
    if provedor not in analise_ia.PROVEDORES:
        return None, {"provedor": "Provedor desconhecido."}
    nova["provedor"] = provedor
    az = nova["azure_openai"]
    if "endpoint" in dados:
        endpoint = str(dados.get("endpoint") or "").strip().rstrip("/")
        if endpoint and not analise_ia.endpoint_azure_valido(endpoint):
            erros["endpoint"] = ("Use o endpoint do recurso: https://<nome>.openai.azure.com "
                                 "(ou .cognitiveservices.azure.com).")
        az["endpoint"] = endpoint
    if "deployment" in dados:
        dep = str(dados.get("deployment") or "").strip()
        if dep and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$", dep):
            erros["deployment"] = "Nome da implantação: letras, números, ponto, hífen e _."
        az["deployment"] = dep
    if "api_version" in dados:
        versao = str(dados.get("api_version") or "").strip() or "v1"
        if not re.match(r"^(v1|\d{4}-\d{2}-\d{2}(-preview)?)$", versao):
            erros["api_version"] = "Use v1 (recomendado) ou uma data como 2024-10-21."
        az["api_version"] = versao
    if "reasoning_effort" in dados:
        esforco = str(dados.get("reasoning_effort") or "")
        az["reasoning_effort"] = esforco if esforco in analise_ia.ESFORCOS else ""
    if "modelo_anthropic" in dados:
        modelo = str(dados.get("modelo_anthropic") or "").strip()
        if modelo and not re.match(r"^[a-z0-9][a-z0-9.\-]{2,59}$", modelo):
            erros["modelo_anthropic"] = "Identificador de modelo inválido."
        nova["anthropic"]["modelo"] = modelo
    if "url_base" in dados:
        url = str(dados.get("url_base") or "").strip().rstrip("/")
        if url and not RE_ENDPOINT_HTTPS.match(url):
            erros["url_base"] = "URL https:// válida."
        nova["openai_compativel"]["url_base"] = url
    if "modelo_compativel" in dados:
        modelo = str(dados.get("modelo_compativel") or "").strip()
        if modelo and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,79}$", modelo):
            erros["modelo_compativel"] = "Identificador de modelo inválido."
        nova["openai_compativel"]["modelo"] = modelo
    if _bool(dados.get("limpar_chave")):
        nova[provedor]["chave_api"] = ""
    chave = str(dados.get("chave_api") or "").strip()
    if chave:
        if not re.match(r"^[A-Za-z0-9_\-.]{16,300}$", chave):
            erros["chave_api"] = "Chave com formato inválido."
        else:
            nova[provedor]["chave_api"] = chave
    return nova, erros


VALIDADORES = {
    "identidade": validar_identidade,
    "departamentos": validar_departamentos,
    "analise": validar_analise,
    "playbook": validar_playbook,
    "saidas": validar_saidas,
    "armazenamento": validar_armazenamento,
    "assinatura": validar_assinatura,
    "ia": validar_ia,
}


# ----------------------------------------------------------------- gravacao

def gravar(caminho, cfg, pasta_backups):
    """Grava de forma atomica, guardando a versao anterior em pasta_backups."""
    os.makedirs(pasta_backups, exist_ok=True)
    backup = None
    if os.path.exists(caminho):
        carimbo = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = os.path.join(pasta_backups, "config_web_%s.json" % carimbo)
        with open(caminho, "rb") as origem, open(backup + ".tmp", "wb") as destino:
            destino.write(origem.read())
        os.replace(backup + ".tmp", backup)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)
    return backup


RE_BACKUP = re.compile(r"^config_web_\d{8}_\d{6}(_\d{1,6})?\.json$")


def listar_backups(pasta_backups):
    if not os.path.isdir(pasta_backups):
        return []
    nomes = sorted((n for n in os.listdir(pasta_backups) if RE_BACKUP.match(n)), reverse=True)
    saida = []
    for nome in nomes:
        caminho = os.path.join(pasta_backups, nome)
        saida.append({"arquivo": nome, "tamanho_kb": round(os.path.getsize(caminho) / 1024, 1),
                      "data": datetime.strptime(nome[11:26], "%Y%m%d_%H%M%S").strftime(
                          "%d/%m/%Y %H:%M:%S")})
    return saida


def sem_segredos(cfg):
    """Copia da config sem nenhum segredo (exportacao / navegador)."""
    limpo = copy.deepcopy(cfg)
    for dep in (limpo.get("docusign") or {}).values():
        for chave in SEGREDOS_DOCUSIGN + ("caminho_chave_privada",):
            if dep.get(chave):
                dep[chave] = ""
    (limpo.get("ia") or {}).pop("chave_api", None)
    return limpo


def status_segredos(cfg):
    """O que o navegador pode saber: so se esta configurado."""
    status = {"docusign": {}}
    for codigo, dep in (cfg.get("docusign") or {}).items():
        status["docusign"][codigo] = {c: bool(dep.get(c)) for c in
                                      SEGREDOS_DOCUSIGN + ("caminho_chave_privada",)}
    return status


def validar_importacao(novo, atual, modelos_por_dep=None):
    """Valida uma configuracao inteira importada; segredos atuais sao mantidos."""
    if not isinstance(novo, dict):
        return None, {"arquivo": "O arquivo não é uma configuração válida."}
    final = copy.deepcopy(atual)
    erros = {}
    if "identidade" in novo and isinstance(novo["identidade"], dict):
        base = identidade_mod.mesclar(final.get("identidade"))   # campo ausente = mantem
        base.update({k: v for k, v in novo["identidade"].items() if v not in (None, "")})
        parte, e = validar_identidade(dict(base, remover_logo=False), final)
        erros.update(e)
        final.update(parte or {})
    if "departamentos" in novo:
        parte, e = validar_departamentos(
            {"departamentos": [d for d in novo["departamentos"] or []
                               if isinstance(d, dict) and not d.get("excluido")]},
            final, modelos_por_dep, permitir_remocao=False)
        erros.update(e)
        final.update(parte or {})
    if "analise" in novo and isinstance(novo["analise"], dict):
        analise = {"global": {}, "departamentos": {}}
        e = {}
        analise["global"] = validar_regras(novo["analise"].get("global") or {}, e, "global.")
        for codigo, valores in (novo["analise"].get("departamentos") or {}).items():
            if codigo in [d["codigo"] for d in final.get("departamentos", [])]:
                analise["departamentos"][codigo] = validar_regras(valores or {}, e,
                                                                  codigo + ".")
        erros.update(e)
        final["analise"] = analise
    if "playbook" in novo:
        parte, e = validar_playbook({"playbook": novo["playbook"]}, final)
        erros.update(e)
        final.update(parte or {})
    if "saidas" in novo:
        parte, e = validar_saidas(novo["saidas"] or {}, final)
        erros.update(e)
        final.update(parte or {})
    parte, e = validar_armazenamento({k: novo.get(k, final.get(k)) for k in (
        "retencao_dias", "tamanho_maximo_mb", "extensoes_aceitas")}, final)
    erros.update(e)
    final.update(parte or {})
    for codigo, dep in (novo.get("docusign") or {}).items():
        if codigo in [d["codigo"] for d in final.get("departamentos", [])] and isinstance(dep, dict):
            dados = {k: v for k, v in dep.items() if k not in SEGREDOS_DOCUSIGN}
            dados["departamento"] = codigo
            parte, e = validar_assinatura(dados, final)
            erros.update({"docusign.%s.%s" % (codigo, k): v for k, v in e.items()})
            final.update(parte or {})
    return final, erros
