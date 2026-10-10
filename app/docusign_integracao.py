# -*- coding: utf-8 -*-
"""
docusign_integracao.py
Envio de contratos para assinatura via DocuSign eSignature API.

Autenticacao: JWT Grant (servidor-a-servidor, sem tela de login).
Posicionamento das assinaturas: por texto-ancora no proprio documento.
"""

import os
import base64
import logging

from docusign_esign import (
    ApiClient, ApiException, EnvelopesApi, EnvelopeDefinition, Document,
    Signer, SignHere, Tabs, Recipients,
)

log = logging.getLogger("docusign")

ESCOPOS = ["signature", "impersonation"]

# ancoras esperadas no modelo padrao, uma por assinante (ver LEIAME_DOCUSIGN.md)
ANCORAS_PADRAO = ["/assinatura_1/", "/assinatura_2/", "/assinatura_3/", "/assinatura_4/"]


class ErroDocuSign(Exception):
    pass


def _ler_chave_privada(cfg):
    caminho = cfg.get("caminho_chave_privada", "")
    if not caminho or not os.path.exists(caminho):
        raise ErroDocuSign(
            "Chave privada RSA nao encontrada em '%s'. Gere o par de chaves no "
            "DocuSign Admin (Apps and Keys > sua Integration Key > Add RSA "
            "Keypair) e salve a chave privada nesse caminho." % caminho)
    with open(caminho, "rb") as f:
        return f.read()


def cliente_autenticado(cfg):
    """Autentica via JWT Grant e devolve um ApiClient pronto para uso."""
    obrigatorios = ["integration_key", "user_id", "account_id", "base_path",
                    "auth_server"]
    faltando = [c for c in obrigatorios if not cfg.get(c)]
    if faltando:
        raise ErroDocuSign(
            "Configuracao da DocuSign incompleta em config_web.json: %s"
            % ", ".join(faltando))

    chave_privada = _ler_chave_privada(cfg)
    api_client = ApiClient()
    api_client.set_base_path(cfg["base_path"])
    api_client.set_oauth_host_name(cfg["auth_server"])

    try:
        resposta = api_client.request_jwt_user_token(
            client_id=cfg["integration_key"],
            user_id=cfg["user_id"],
            oauth_host_name=cfg["auth_server"],
            private_key_bytes=chave_privada,
            expires_in=3600,
            scopes=ESCOPOS,
        )
    except ApiException as erro:
        corpo = getattr(erro, "body", b"")
        texto = corpo.decode() if isinstance(corpo, (bytes, bytearray)) else str(corpo)
        if "consent_required" in texto:
            url_consentimento = (
                "https://%s/oauth/auth?response_type=code&scope=%s"
                "&client_id=%s&redirect_uri=https://www.docusign.com"
                % (cfg["auth_server"], "%20".join(ESCOPOS), cfg["integration_key"]))
            raise ErroDocuSign(
                "E preciso autorizar a aplicacao uma unica vez. Abra este link, "
                "logue com o usuario configurado e aceite o consentimento:\n%s"
                % url_consentimento)
        raise ErroDocuSign("Falha na autenticacao JWT: %s" % texto)

    api_client.set_default_header("Authorization",
                                  "Bearer " + resposta.access_token)
    return api_client


def _montar_signatario(nome, email, ordem, indice_ancora):
    ancora = ANCORAS_PADRAO[indice_ancora % len(ANCORAS_PADRAO)]
    assinar_aqui = SignHere(
        anchor_string=ancora,
        anchor_units="pixels",
        anchor_x_offset="0", anchor_y_offset="0",
        anchor_ignore_if_not_present="true",
    )
    return Signer(
        email=email, name=nome,
        recipient_id=str(indice_ancora + 1),
        routing_order=str(ordem),
        tabs=Tabs(sign_here_tabs=[assinar_aqui]),
    )


def validar_config(cfg):
    obrigatorios = ["integration_key", "user_id", "account_id", "base_path",
                    "auth_server"]
    faltando = [c for c in obrigatorios if not cfg.get(c)]
    if faltando:
        raise ErroDocuSign(
            "Configuracao da DocuSign incompleta em config_web.json: %s"
            % ", ".join(faltando))


def enviar_envelope(cfg, caminho_arquivo, assunto, signatarios, mensagem=""):
    """
    signatarios: lista de dicts {"nome": str, "email": str, "ordem": int}
    ordem igual entre todos = assinatura em paralelo.
    ordem crescente = assinatura sequencial (1 assina, depois 2, etc).
    Retorna o envelope_id.
    """
    validar_config(cfg)
    if not signatarios:
        raise ErroDocuSign("Informe ao menos um signatário.")
    if len(signatarios) > len(ANCORAS_PADRAO):
        raise ErroDocuSign(
            "Máximo de %d signatários por contrato nesta configuração "
            "(o modelo tem %d âncoras de assinatura)."
            % (len(ANCORAS_PADRAO), len(ANCORAS_PADRAO)))
    if not os.path.exists(caminho_arquivo):
        raise ErroDocuSign("Arquivo não encontrado: %s" % caminho_arquivo)

    extensao = os.path.splitext(caminho_arquivo)[1].lower().lstrip(".")
    formatos = {"docx": "docx", "pdf": "pdf", "txt": "txt"}
    if extensao not in formatos:
        raise ErroDocuSign("Formato de arquivo não suportado pela DocuSign: %s"
                          % extensao)

    with open(caminho_arquivo, "rb") as f:
        conteudo_b64 = base64.b64encode(f.read()).decode("ascii")

    documento = Document(
        document_base64=conteudo_b64,
        name=os.path.basename(caminho_arquivo),
        file_extension=extensao,
        document_id="1",
    )

    signers = [_montar_signatario(s["nome"], s["email"], s.get("ordem", 1), i)
              for i, s in enumerate(signatarios)]

    envelope = EnvelopeDefinition(
        email_subject=assunto[:100],
        email_blurb=mensagem[:1000] if mensagem else None,
        documents=[documento],
        recipients=Recipients(signers=signers),
        status="sent",
    )

    api_client = cliente_autenticado(cfg)
    envelopes_api = EnvelopesApi(api_client)
    try:
        resultado = envelopes_api.create_envelope(cfg["account_id"],
                                                   envelope_definition=envelope)
    except ApiException as erro:
        corpo = getattr(erro, "body", b"")
        texto = corpo.decode() if isinstance(corpo, (bytes, bytearray)) else str(corpo)
        log.error("Falha ao criar envelope: %s", texto)
        raise ErroDocuSign("A DocuSign recusou o envio: %s" % texto)

    return resultado.envelope_id


MAPA_STATUS = {
    "sent": "enviado", "delivered": "visualizado", "completed": "assinado",
    "declined": "recusado", "voided": "cancelado", "created": "criado",
}


def consultar_status(cfg, envelope_id):
    api_client = cliente_autenticado(cfg)
    envelopes_api = EnvelopesApi(api_client)
    try:
        envelope = envelopes_api.get_envelope(cfg["account_id"], envelope_id)
    except ApiException as erro:
        corpo = getattr(erro, "body", b"")
        texto = corpo.decode() if isinstance(corpo, (bytes, bytearray)) else str(corpo)
        raise ErroDocuSign("Falha ao consultar status: %s" % texto)
    return MAPA_STATUS.get(envelope.status, envelope.status)


def baixar_documento_assinado(cfg, envelope_id, destino):
    api_client = cliente_autenticado(cfg)
    envelopes_api = EnvelopesApi(api_client)
    conteudo = envelopes_api.get_document(cfg["account_id"], "combined", envelope_id)
    with open(destino, "wb") as f:
        if isinstance(conteudo, str):
            with open(conteudo, "rb") as origem:
                f.write(origem.read())
        else:
            f.write(conteudo)
    return destino
