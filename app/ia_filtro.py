# -*- coding: utf-8 -*-
"""
ia_filtro.py
Filtro das perguntas do assistente de IA, aplicado NO SERVIDOR antes de qualquer
chamada a API. Pergunta barrada aqui nao gasta nenhum token.

Camadas (na ordem):
  1. limpeza     - remove caracteres invisiveis/controle, normaliza Unicode
  2. tamanho     - vazia, curta demais, longa demais (DoS / colagem de texto)
  3. ruido       - repeticao de caracteres, pouca letra, blocos de codigo/base64
  4. injecao     - "ignore as instrucoes", "voce agora e", "prompt do sistema",
                   jailbreak/DAN, marcadores de papel (<|system|>, ### instrucao) ...
  5. fora do     - conta pura ("quanto e 1+1"), receitas, piadas, poemas, programacao,
     escopo        esportes...; no modo "estrito" a pergunta precisa citar o contrato
                   ou um termo juridico/contratual (lista abaixo + palavras do proprio
                   contrato + termos extras de Ajustes)
Limites de frequencia (DoS) ficam em LimitadorTaxa.

Documentacao: docs/ia.md
"""

import re
import time
import threading
import unicodedata

# ---------------------------------------------------------------- vocabulario

TERMOS_DOMINIO = """
contrato contratos contratual contratuais contratante contratada contratado fornecedor
fornecedora prestador prestadora tomador cliente parte partes clausula clausulas
paragrafo inciso alinea anexo anexos aditivo aditamento minuta modelo padrao
devolucao devolvido devolvida alteracao alteracoes alterou alterado alterada alterados
mudanca mudancas mudou incluiu incluido incluida removeu removido removida suprimido
suprimida moveu movido trecho trechos redacao ocorrencia ocorrencias comparacao
comparativo preenchido preenchidos preencher preenchimento pendente pendentes pendencia pendencias campo campos
critica criticas critico criticos risco riscos arriscado playbook politica
multa multas penalidade penalidades sancao sancoes juros correcao reajuste reajustes
indice ipca igpm igp inpc selic pagamento pagamentos faturamento fatura nota fiscal
boleto parcela parcelas mensalidade implantacao valor valores preco precos custo custos
remuneracao honorarios mensal anual desconto retencao glosa tributo tributos imposto impostos iss
vigencia prazo prazos renovacao renovacoes prorrogacao prorrogar vencimento
inicio encerramento rescisao rescindir resilicao resolucao denuncia distrato aviso
notificacao notificar previo inadimplemento inadimplencia mora descumprimento
obrigacao obrigacoes responsabilidade responsabilidades solidaria subsidiaria
indenizacao indenizacoes indenizar ressarcimento perdas danos lucros cessantes limitacao limite teto
garantia garantias caucao fianca seguro seguros apolice
confidencialidade sigilo segredo lgpd dados pessoais protecao privacidade
propriedade intelectual licenca licenciamento direitos autorais software marca
exclusividade subcontratacao subcontratar cessao ceder transferencia terceiros
foro arbitragem mediacao jurisdicao comarca litigio
sla nivel servico servicos disponibilidade atendimento chamado chamados suporte
entrega entregas escopo aceite homologacao cronograma marco
forca maior caso fortuito anticorrupcao compliance integridade trabalhista
previdenciario vinculo empregaticio assinatura assinaturas testemunha testemunhas
representante representantes procuracao cnpj cpf razao social endereco
contraproposta negociar negociacao aceitar rejeitar aprovar aprovacao parecer juridico
juridica juridicos legal lei leis civil clt cdc jurisprudencia
""".split()

PREFIXOS_DOMINIO = ("contrat", "clausul", "rescis", "rescind", "indeniz", "penalid", "reajust",
                    "vigenc", "garant", "responsab", "confidenc", "obrigac", "inadimpl",
                    "prorrog", "renov", "notific", "subcontrat", "multa", "fornec",
                    "juridic", "alterac", "devoluc", "pagament", "juros", "tribut")

# pedidos claramente fora do objetivo (so barram quando nao ha termo do dominio,
# exceto os "sempre fora", que barram mesmo com termo do dominio)
FORA_SEMPRE = [
    (r"\b(receita|culinaria|bolo|pizza|lasanha|churrasco)\b", "receitas/culinária"),
    (r"\b(piada|piadas|anedota|trocadilho)\b", "piadas"),
    (r"\b(poema|poesia|soneto|haicai|rima|rimas|letra de musica|cancao|musica)\b",
     "textos criativos"),
    (r"\b(conto|historinha|fanfic|roteiro de filme)\b", "textos criativos"),
    (r"\b(horoscopo|signo|tarot|loteria|aposta|apostas)\b", "assuntos pessoais"),
]
FORA_SEM_DOMINIO = [
    (r"\b(python|javascript|java|html|css|sql|programa|programacao|codigo fonte|script)\b",
     "programação"),
    (r"\b(futebol|jogo|jogos|campeonato|time|filme|filmes|serie|series|novela)\b",
     "entretenimento"),
    (r"\b(clima|tempo amanha|previsao do tempo|temperatura)\b", "clima"),
    (r"\b(traduza|traducao|translate)\b", "tradução"),
    (r"\b(capital de|presidente|historia do brasil|geografia)\b", "conhecimentos gerais"),
    (r"\b(saude|doenca|remedio|dieta|exercicio)\b", "saúde"),
]

# ---------------------------------------------------------------- injecao / jailbreak

VERBOS_IGNORAR = (r"(ignore|ignora|ignorar|ignorem|desconsidere|desconsiderar|desconsidera"
                  r"|esqueca|esquecer|esquece|descarte|descartar|pule|pular|burle|burlar"
                  r"|contorne|contornar|anule|anular|substitua|substituir)")
PADROES_INJECAO = [
    r"\b%s\b.{0,40}\b(instruc\w*|regra\w*|orienta\w*|diretriz\w*|prompt|comando\w*"
    r"|restric\w*|politica\w*|acima|anterior\w*|tudo)" % VERBOS_IGNORAR,
    r"\bignore\b.{0,30}\b(previous|above|prior|all|the)\b",
    r"\b(disregard|forget|override|bypass)\b.{0,30}\b(instruction|rule|prompt|polic)",
    r"\b(system|developer)\s*(prompt|message|mode)\b",
    r"\bprompt\s+(do|de)\s+sistema\b", r"\bmensagem\s+(do|de)\s+sistema\b",
    r"\b(suas|tuas)\s+(instrucoes|regras|diretrizes|configuracoes)\b",
    r"\binstrucoes\s+(do sistema|internas|originais|iniciais|ocultas|secretas)\b",
    r"\b(revele|revelar|mostre|mostrar|exiba|exibir|repita|repetir|imprima|copie|vaze|reveal"
    r"|print|show|repeat)\b.{0,40}\b(prompt|texto acima|mensagem inicial|system|instrucoes)\b",
    r"\b(voce|tu)\s+(agora\s+)?(e|sera|vai ser|passa a ser)\s+(um|uma)\b",
    r"\ba partir de agora\b", r"\bde agora em diante\b", r"\bfrom now on\b",
    r"\b(aja como|atue como|comporte-se como|finja|fingir|imagine que voce|faca de conta"
    r"|roleplay|role play|pretend|act as|you are now|simule ser)\b",
    r"\b(modo|mode)\s+(desenvolvedor|developer|dan|deus|god|irrestrito|sem filtro|jailbreak)",
    r"\bjail\s?break\b", r"\bdo anything now\b", r"\bdan\s+(mode|prompt)\b",
    r"\b(responda|responder|fale|seja|aja)\b.{0,20}\bsem\s+(restric\w*|filtro\w*|censura"
    r"|limites|regras)\b",
    r"\bno\s+(restrictions|filters)\b",
    r"\bnovas?\s+instruc\w*\b", r"\bnew\s+instructions?\b",
    r"<\|?\s*(system|assistant|user|im_start|im_end)\s*\|?>", r"\[\s*(system|inst)\s*\]",
    r"#{2,}\s*(instruc|system|sistema)", r"\bbegin\s+(system|prompt)\b",
    r"</?\s*(contrato|pergunta)\s*>",
]

RE_INJECAO = [re.compile(p, re.IGNORECASE) for p in PADROES_INJECAO]
RE_FORA_SEMPRE = [(re.compile(p), m) for p, m in FORA_SEMPRE]
RE_FORA_SEM_DOMINIO = [(re.compile(p), m) for p, m in FORA_SEM_DOMINIO]
RE_CONTA = re.compile(r"^(quanto\s+(e|da|vale|sao)\s+|calcule\s+|resolva\s+|qual\s+(e\s+)?o\s+"
                      r"resultado\s+de\s+)?[\d\s+\-*/x÷^().,=?%]+$")
RE_TEM_CONTA = re.compile(r"\d+\s*[+\-*/x÷^]\s*\d+")
RE_INVISIVEIS = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]")
RE_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
RE_REPETICAO = re.compile(r"(.)\1{9,}")
RE_BASE64 = re.compile(r"[A-Za-z0-9+/=]{60,}")
RE_PALAVRA = re.compile(r"[a-z0-9]{3,}")
RE_REFERENCIA = re.compile(r"#\s?\d{1,4}\b|\b(ocorrencia|item|clausula)\s+\d+", re.IGNORECASE)

MENSAGENS = {
    "vazia": "Escreva uma pergunta.",
    "curta": "Pergunta curta demais. Descreva o que quer saber sobre o contrato.",
    "longa": "Pergunta longa demais (máximo de {max} caracteres). Seja mais direto.",
    "ruido": "Não entendi a pergunta. Escreva em texto simples, sem códigos ou repetições.",
    "injecao": ("Pedido bloqueado: parece uma tentativa de mudar as regras do assistente. "
                "Pergunte algo sobre o contrato."),
    "fora_escopo": ("Fora do escopo ({motivo}). O assistente responde só sobre este contrato "
                    "e termos jurídicos relacionados."),
    "bloqueado": "Pergunta com termo bloqueado pela configuração ({motivo}).",
}


def remover_acentos(texto):
    t = unicodedata.normalize("NFD", texto)
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def limpar(texto):
    """Texto que segue adiante (enviado a API): sem invisiveis/controle, espacos normais."""
    t = unicodedata.normalize("NFKC", str(texto or ""))
    t = RE_INVISIVEIS.sub("", t)
    t = RE_CONTROLE.sub(" ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def normalizar(texto):
    """Forma de comparacao: minusculas, sem acento, sem pontuacao repetida."""
    t = remover_acentos(limpar(texto).lower())
    return re.sub(r"\s+", " ", t)


def vocabulario_contrato(resultado, limite=1500):
    """Palavras (5+ letras) de clausulas e trechos alterados: perguntas que citam o
    proprio contrato passam no modo estrito mesmo sem termo juridico."""
    palavras = set()
    for o in (resultado or {}).get("ocorrencias", []):
        for campo in ("clausula", "texto_devolucao", "texto_modelo"):
            for p in re.findall(r"[a-z]{5,}", normalizar(o.get(campo) or "")):
                palavras.add(p)
                if len(palavras) >= limite:
                    return palavras
    meta = (resultado or {}).get("meta", {})
    palavras.update(re.findall(r"[a-z]{4,}", normalizar(meta.get("fornecedor") or "")))
    return palavras


def tem_termo_dominio(norm, extras=(), vocab=()):
    palavras = set(RE_PALAVRA.findall(norm))
    if palavras & set(TERMOS_DOMINIO):
        return True
    if any(p.startswith(PREFIXOS_DOMINIO) for p in palavras):
        return True
    if RE_REFERENCIA.search(norm):
        return True
    extras_norm = {normalizar(e) for e in extras if e}
    if any(e and e in norm for e in extras_norm):
        return True
    return bool(palavras & set(vocab or ()))


def avaliar(pergunta, cfg=None, vocab_contrato=()):
    """-> dict {ok, codigo, mensagem, texto}. `texto` e a pergunta limpa (so ela vai a API)."""
    cfg = cfg or {}
    maximo = int(cfg.get("chat_max_caracteres") or 500)
    rigor = cfg.get("filtro_rigor") or "estrito"
    texto = limpar(pergunta)
    norm = normalizar(texto)

    def barrar(codigo, **fmt):
        return {"ok": False, "codigo": codigo, "texto": texto,
                "mensagem": MENSAGENS[codigo].format(max=maximo, **fmt)}

    # 1-2. tamanho
    if not norm:
        return barrar("vazia")
    if len(texto) > maximo:
        return barrar("longa")
    letras = sum(1 for c in norm if c.isalpha())
    if len(norm) < 6 or letras < 4:
        # "1+1" e parecidos caem aqui ou na regra de conta abaixo
        if RE_TEM_CONTA.search(norm) or RE_CONTA.match(norm):
            return barrar("fora_escopo", motivo="cálculo")
        return barrar("curta")

    # 3. ruido
    if RE_REPETICAO.search(norm) or RE_BASE64.search(texto) or "```" in texto:
        return barrar("ruido")
    if letras / max(1, len(norm.replace(" ", ""))) < 0.5:
        return barrar("ruido")
    palavras = norm.split()
    if len(palavras) >= 12 and len(set(palavras)) / len(palavras) < 0.35:
        return barrar("ruido")

    # 4. injecao / jailbreak (antes de tudo o que for de dominio: "ignore as clausulas...")
    for regex in RE_INJECAO:
        if regex.search(norm):
            return barrar("injecao")

    # termos bloqueados pela configuracao
    for termo in cfg.get("termos_bloqueados") or []:
        t = normalizar(termo)
        if t and re.search(r"\b%s\b" % re.escape(t), norm):
            return barrar("bloqueado", motivo=termo)

    # 5. fora do escopo
    if RE_CONTA.match(norm):
        return barrar("fora_escopo", motivo="cálculo")
    for regex, motivo in RE_FORA_SEMPRE:
        if regex.search(norm):
            return barrar("fora_escopo", motivo=motivo)
    dominio = tem_termo_dominio(norm, cfg.get("termos_permitidos") or (), vocab_contrato)
    if not dominio:
        if RE_TEM_CONTA.search(norm):
            return barrar("fora_escopo", motivo="cálculo")
        for regex, motivo in RE_FORA_SEM_DOMINIO:
            if regex.search(norm):
                return barrar("fora_escopo", motivo=motivo)
        if rigor == "estrito":
            return barrar("fora_escopo", motivo="não cita o contrato nem termo jurídico")
    return {"ok": True, "codigo": "ok", "mensagem": "", "texto": texto}


def chave_cache(texto):
    """Perguntas iguais (ignorando caixa, acento e pontuacao) usam a mesma resposta."""
    return re.sub(r"[^a-z0-9 ]", "", normalizar(texto)).strip()


# ---------------------------------------------------------------- limites (DoS)

class LimitadorTaxa:
    """Janela deslizante em memoria: no maximo `limite` eventos por `janela` segundos."""

    def __init__(self):
        self._eventos = {}
        self._trava = threading.Lock()

    def permitir(self, chave, limite, janela=60.0, agora=None):
        """-> (ok, segundos_para_liberar). limite <= 0 = sem limite."""
        if not limite or limite <= 0:
            return True, 0
        agora = time.time() if agora is None else agora
        with self._trava:
            eventos = [t for t in self._eventos.get(chave, []) if agora - t < janela]
            if len(eventos) >= limite:
                self._eventos[chave] = eventos
                return False, int(janela - (agora - eventos[0])) + 1
            eventos.append(agora)
            self._eventos[chave] = eventos
            if len(self._eventos) > 5000:                 # nao cresce sem limite
                for k in list(self._eventos)[:1000]:
                    self._eventos.pop(k, None)
            return True, 0
