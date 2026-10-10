# -*- coding: utf-8 -*-
"""
identidade.py
Identidade visual configuravel (nome, organizacao, cor, logo) compartilhada entre
o motor (relatorios) e a versao web. Nenhuma marca fica fixa no codigo.

A partir de uma unica cor primaria sao derivados os tokens semanticos usados no CSS
e nos relatorios, com checagem de contraste WCAG AA.
"""

import os
import re
import json
import html
import base64
import colorsys

IDENTIDADE_PADRAO = {
    "nome_produto": "Comparador de Contratos",
    "nome_curto": "Comparador",
    "organizacao_contratante": "Minha Organização",
    "cor_primaria": "#2F5BEA",
    "logo": "",   # vazio = monograma; ou caminho .svg/.png em dados/identidade
    "rodape_relatorio": "Uso interno",
}

# fundos de referencia dos temas (mesmos valores de static/estilo.css)
FUNDO_CLARO = "#ffffff"
FUNDO_ESCURO = "#1e2125"
AA_TEXTO = 4.5
AA_GRANDE = 3.0

RE_COR = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


# ------------------------------------------------------------------ cores

def normalizar_cor(cor, padrao=IDENTIDADE_PADRAO["cor_primaria"]):
    m = RE_COR.match((cor or "").strip())
    if not m:
        return padrao.upper()
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.upper()


def cor_valida(cor):
    return bool(RE_COR.match((cor or "").strip()))


def _rgb(cor):
    h = normalizar_cor(cor).lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(c * 255))) for c in rgb)


def luminancia(cor):
    def canal(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (canal(c) for c in _rgb(cor))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(cor_a, cor_b):
    la, lb = luminancia(cor_a), luminancia(cor_b)
    claro, escuro = max(la, lb), min(la, lb)
    return round((claro + 0.05) / (escuro + 0.05), 2)


def misturar(cor_a, cor_b, peso_b):
    a, b = _rgb(cor_a), _rgb(cor_b)
    return _hex(tuple(x * (1 - peso_b) + y * peso_b for x, y in zip(a, b)))


def _ajustar_luz(cor, delta):
    h, l, s = colorsys.rgb_to_hls(*_rgb(cor))
    return _hex(colorsys.hls_to_rgb(h, max(0.0, min(1.0, l + delta)), s))


def ate_contraste(cor, fundo, minimo, direcao):
    """Escurece (direcao=-1) ou clareia (+1) ate atingir o contraste minimo."""
    atual = normalizar_cor(cor)
    for _ in range(60):
        if contraste(atual, fundo) >= minimo:
            return atual
        atual = _ajustar_luz(atual, 0.02 * direcao)
    return atual


def gerar_tokens(cor_primaria):
    """Tokens semanticos para os temas claro e escuro + avisos de contraste."""
    base = normalizar_cor(cor_primaria)
    avisos = []

    # botao: fundo primaria com texto branco precisa de AA
    botao = ate_contraste(base, "#FFFFFF", AA_TEXTO, -1)
    if botao != base:
        avisos.append("A cor %s tem contraste %.2f:1 com texto branco; os botões usam "
                      "a variação %s (%.2f:1) para atender AA."
                      % (base, contraste(base, "#FFFFFF"), botao,
                         contraste(botao, "#FFFFFF")))
    forte = ate_contraste(misturar(botao, "#000000", 0.18), FUNDO_CLARO, 7.0, -1)
    claro = {
        "primaria": botao,
        "primaria-forte": forte,
        "primaria-suave": misturar(base, "#FFFFFF", 0.90),
        "primaria-borda": misturar(base, "#FFFFFF", 0.65),
        "foco": misturar(base, "#FFFFFF", 0.55),
        "sobre-primaria": "#FFFFFF",
    }

    texto_escuro = ate_contraste(misturar(base, "#FFFFFF", 0.25), FUNDO_ESCURO, AA_TEXTO, 1)
    escuro = {
        "primaria": botao,
        "primaria-forte": texto_escuro,
        "primaria-suave": misturar(FUNDO_ESCURO, base, 0.22),
        "primaria-borda": misturar(FUNDO_ESCURO, base, 0.50),
        "foco": misturar(base, "#FFFFFF", 0.35),
        "sobre-primaria": "#FFFFFF",
    }

    verificacoes = {
        "botao_texto_branco": contraste(botao, "#FFFFFF"),
        "texto_forte_fundo_claro": contraste(forte, FUNDO_CLARO),
        "texto_forte_fundo_escuro": contraste(texto_escuro, FUNDO_ESCURO),
        "texto_forte_sobre_suave": contraste(forte, claro["primaria-suave"]),
    }
    for nome, valor in verificacoes.items():
        if valor < AA_TEXTO:
            avisos.append("Contraste abaixo de AA em %s: %.2f:1" % (nome, valor))

    return {"base": base, "claro": claro, "escuro": escuro,
            "contraste": verificacoes, "avisos": avisos,
            # cor usada nos cabecalhos de tabela de PDF/XLSX (texto branco)
            "relatorio_cabecalho": forte}


def css_variaveis(tokens):
    """Bloco <style> com os tokens para os temas claro e escuro."""
    def bloco(seletor, valores):
        corpo = "".join("--%s:%s;" % (k, v) for k, v in valores.items())
        return "%s{%s}" % (seletor, corpo)
    return (bloco(":root", tokens["claro"]) + "\n"
            + bloco('html[data-tema="escuro"]', tokens["escuro"]))


# ------------------------------------------------------------------ identidade

def mesclar(identidade=None):
    final = dict(IDENTIDADE_PADRAO)
    for chave, valor in (identidade or {}).items():
        if chave in final and valor not in (None,):
            final[chave] = valor
    final["cor_primaria"] = normalizar_cor(final["cor_primaria"])
    for chave in ("nome_produto", "nome_curto", "organizacao_contratante"):
        final[chave] = (final[chave] or IDENTIDADE_PADRAO[chave]).strip()[:80]
    final["rodape_relatorio"] = (final.get("rodape_relatorio") or "").strip()[:300]
    return final


def iniciais(nome):
    """Monograma: iniciais das palavras significativas (max. 2)."""
    ignorar = {"de", "da", "do", "das", "dos", "e", "a", "o", "para", "em"}
    palavras = [p for p in re.split(r"[\s\-_/]+", nome or "") if p]
    uteis = [p for p in palavras if p.lower() not in ignorar] or palavras or ["C"]
    return "".join(p[0] for p in uteis[:2]).upper()


def logo_svg(nome_produto, cor_primaria, tamanho=64):
    """Monograma neutro: quadrado arredondado na cor primaria com as iniciais."""
    cor = ate_contraste(normalizar_cor(cor_primaria), "#FFFFFF", AA_TEXTO, -1)
    texto = html.escape(iniciais(nome_produto))
    fonte = 26 if len(texto) > 1 else 32
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="%d" '
        'height="%d" role="img" aria-label="%s">'
        '<rect width="64" height="64" rx="14" fill="%s"/>'
        '<rect x="7" y="7" width="50" height="50" rx="9" fill="none" '
        'stroke="#FFFFFF" stroke-opacity=".28" stroke-width="2"/>'
        '<text x="32" y="33" text-anchor="middle" dominant-baseline="central" '
        'font-family="Segoe UI, Arial, sans-serif" font-size="%d" font-weight="700" '
        'fill="#FFFFFF" letter-spacing="1">%s</text></svg>'
        % (tamanho, tamanho, html.escape(nome_produto or ""), cor, fonte, texto))


def logo_data_uri(identidade, caminho_logo=None):
    """data: URI do logo (arquivo configurado ou monograma), para relatorios HTML."""
    if caminho_logo:
        try:
            with open(caminho_logo, "rb") as f:
                dados = f.read()
            tipo = "image/svg+xml" if caminho_logo.lower().endswith(".svg") else "image/png"
            return "data:%s;base64,%s" % (tipo, base64.b64encode(dados).decode("ascii"))
        except OSError:
            pass
    svg = logo_svg(identidade["nome_produto"], identidade["cor_primaria"])
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


# ------------------------------------------------------------------ perfis

RE_ID_PERFIL = re.compile(r"^[a-z0-9_]{2,40}$")
PREFIXO_LOGO_PERFIL = "perfil:"


def listar_perfis(pasta_perfis):
    """Perfis prontos em <pasta_perfis>/<id>/perfil.json (marcas ficam fora do codigo)."""
    perfis = []
    if not pasta_perfis or not os.path.isdir(pasta_perfis):
        return perfis
    for nome in sorted(os.listdir(pasta_perfis)):
        arquivo = os.path.join(pasta_perfis, nome, "perfil.json")
        if not RE_ID_PERFIL.match(nome) or not os.path.isfile(arquivo):
            continue
        try:
            with open(arquivo, encoding="utf-8") as f:
                bruto = json.load(f)
        except (OSError, ValueError):
            continue
        ident = dict(bruto.get("identidade") or {})
        logo = os.path.basename(str(ident.get("logo") or "").strip())
        ident["logo"] = (PREFIXO_LOGO_PERFIL + nome + "/" + logo) if logo else ""
        perfis.append({
            "id": nome,
            "ordem": bruto.get("ordem", 99),
            "rotulo": str(bruto.get("rotulo") or nome)[:40],
            "descricao": str(bruto.get("descricao") or "")[:120],
            "identidade": mesclar(ident),
        })
    perfis.sort(key=lambda p: (p["ordem"], p["id"]))
    return perfis


def caminho_logo(logo, pasta_dados, pasta_perfis):
    """Resolve o valor de identidade.logo para um arquivo .svg/.png existente, ou None.
    Aceita "perfil:<id>/<arquivo>" (pasta de perfis) ou caminho relativo a dados/."""
    logo = (logo or "").strip()
    if not logo:
        return None
    if logo.startswith(PREFIXO_LOGO_PERFIL):
        resto = logo[len(PREFIXO_LOGO_PERFIL):]
        partes = resto.split("/", 1)
        if len(partes) != 2 or not RE_ID_PERFIL.match(partes[0]):
            return None
        base = os.path.abspath(os.path.join(pasta_perfis, partes[0]))
        caminho = os.path.abspath(os.path.join(base, os.path.basename(partes[1])))
        if not caminho.startswith(base + os.sep):
            return None
    else:
        caminho = logo if os.path.isabs(logo) else os.path.join(pasta_dados, logo)
        caminho = os.path.abspath(caminho)
    if not caminho.lower().endswith((".svg", ".png")) or not os.path.isfile(caminho):
        return None
    return caminho
