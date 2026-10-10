# -*- coding: utf-8 -*-
"""
comparativo_interativo.py
Monta o comparativo.html interativo a partir do resultado estruturado.

Arquivo unico e autocontido: CSS, JavaScript, logo e dados embutidos, zero CDN.
Abre offline com duplo clique e tambem dentro do painel do sistema (onde a revisao
passa a ser salva no servidor, via /resultado/<id>/revisao).

Fontes editaveis: relatorio_interativo/template.html, estilo.css, app.js
"""

import os
import json
import html

import identidade as identidade_mod

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "relatorio_interativo")


def _ler(nome):
    with open(os.path.join(PASTA, nome), encoding="utf-8") as f:
        return f.read()


def json_para_script(dados):
    """JSON seguro dentro de <script type="application/json">."""
    texto = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    return (texto.replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def montar(resultado, cfg=None):
    cfg = cfg or {}
    ident = identidade_mod.mesclar(cfg.get("identidade") or resultado.get("identidade"))
    tokens = identidade_mod.gerar_tokens(ident["cor_primaria"])
    logo = identidade_mod.logo_data_uri(ident, cfg.get("caminho_logo") or None)
    dados = dict(resultado)
    dados["identidade"] = ident
    # campos so para o PDF/TXT nao precisam ir para o navegador
    dados.pop("config_relevante", None)
    meta = resultado.get("meta", {})
    titulo = "%s — %s" % (ident["nome_produto"], meta.get("fornecedor", ""))
    substituicoes = {
        "__TOKENS__": identidade_mod.css_variaveis(tokens),
        "__CSS__": _ler("estilo.css"),
        "__JS__": _ler("app.js").replace("</script", "<\\/script"),
        "__DADOS__": json_para_script(dados),
        "__TITULO__": html.escape(titulo),
        "__PRODUTO__": html.escape(ident["nome_produto"]),
        "__LOGO__": logo,
        "__GERADOR__": html.escape("%s · motor v%s" % (ident["nome_produto"],
                                                       meta.get("versao_motor", ""))),
    }
    saida = _ler("template.html")
    # dados por ultimo: o JSON pode conter textos parecidos com os marcadores
    for chave in ("__TOKENS__", "__CSS__", "__JS__", "__TITULO__", "__PRODUTO__",
                  "__LOGO__", "__GERADOR__", "__DADOS__"):
        saida = saida.replace(chave, substituicoes[chave])
    return saida
