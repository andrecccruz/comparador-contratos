# Comparador de Contratos

Ferramenta web local (Python + Flask) que compara a versão devolvida de um contrato com o
modelo padrão e mostra, cláusula por cláusula, o que foi **preenchido, alterado, incluído,
removido ou movido**, com uma nota de risco geral.

Tudo roda na própria máquina: nenhum contrato é enviado para fora.

## Recursos

- Comparação de `.docx`, `.pdf` e `.txt` contra um modelo padrão por departamento
- Detecção de campos preenchidos, cláusulas alteradas, incluídas, removidas e movidas
- Pontuação de risco por ocorrência e geral, com regras (playbook) configuráveis
- Saídas: relatório HTML interativo com filtros, PDF, planilha Excel, resumo TXT,
  contraproposta em Word com marcas de revisão (redline) e parecer em PDF/DOCX
- Comparação em lote, rodadas de negociação e histórico com status de revisão
- Painel com indicadores
- Identidade visual configurável (nome, cor, logo e rodapé), com checagem de contraste WCAG AA
- Modo claro e escuro
- Opcional: análise por IA (Azure OpenAI, Anthropic ou API compatível com OpenAI) e
  envio para assinatura via DocuSign

## Requisitos

- Python 3.10 ou superior (testado com 3.12)

## Como rodar

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app/web/app.py
```

Abra http://127.0.0.1:5000. No primeiro uso o sistema leva para **Ajustes → Departamentos**:

1. Cadastre um departamento (ex.: código `JUR`, nome `Jurídico`).
2. Em **Modelos**, envie o contrato-modelo padrão.
3. Em **Comparar**, anexe a versão devolvida e clique em **Comparar**.

Os dados (modelos, envios, resultados, backups) ficam em `app/web/dados/`, que está no
`.gitignore`.

### Linha de comando

O motor também roda sozinho, comparando todos os arquivos de uma pasta:

```bash
python app/comparar_contratos.py   # na 1ª vez cria app/config.json; revise os caminhos
python app/comparar_contratos.py   # compara dados/devolucoes/* contra dados/modelo_padrão/
```

### Servidor Linux (VPS)

A fila de comparações é uma thread em memória, então use **um** worker com várias threads:

```bash
pip install gunicorn
cd app/web
gunicorn -w 1 --threads 8 -b 127.0.0.1:5000 wsgi:app
```

Coloque um Nginx (ou similar) na frente para HTTPS.

## Identidade visual

O padrão é um azul neutro (`#2F5BEA`) com monograma gerado a partir do nome do produto.
Tudo pode ser trocado em **Ajustes → Identidade**, sem mexer no código. A partir da cor
primária o sistema deriva os tokens de cor dos dois temas e ajusta o tom dos botões quando
necessário para manter contraste AA.

Para distribuir um tema pronto, crie `app/perfis/<id>/perfil.json` (veja
`app/perfis/padrao/perfil.json`) e, se quiser, coloque o logo `.svg`/`.png` na mesma pasta.

## Recursos opcionais

### Análise por IA

Desligada por padrão. Para ativar:

```bash
export CONTRATOS_IA_DISPONIVEL=1
```

Depois configure o provedor em **Ajustes → IA**, ou por variáveis de ambiente (que têm
prioridade sobre o arquivo):

| Provedor | Variáveis |
|---|---|
| Azure OpenAI | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Compatível com OpenAI | `OPENAI_API_KEY` |

O texto enviado passa antes por um filtro (`app/ia_filtro.py`) contra injeção de instruções.
Nenhuma chave é gravada no repositório.

### Assinatura eletrônica (DocuSign)

```bash
pip install -r requirements-opcional.txt
```

A aba **Assinatura eletrônica** só aparece quando a biblioteca `docusign-esign` está
instalada. As credenciais são configuradas por departamento.

## Variáveis de ambiente

| Variável | Uso |
|---|---|
| `CONTRATOS_IA_DISPONIVEL` | `1` ativa os recursos de IA |
| `CONTRATOS_CONFIG_WEB` | Caminho alternativo do `config_web.json` |
| `CONTRATOS_PASTA_PERFIS` | Pasta alternativa de perfis de identidade |

## Estrutura

```
app/
├── comparar_contratos.py      motor de comparação (também roda via linha de comando)
├── relatorios.py              PDF, Excel, HTML e TXT
├── comparativo_interativo.py  relatório HTML com filtros (usa relatorio_interativo/)
├── redline.py                 contraproposta .docx com marcas de revisão
├── parecer.py                 parecer em PDF/DOCX
├── identidade.py              identidade visual e tokens de cor
├── analise_ia.py, ia_filtro.py  IA opcional
├── docusign_integracao.py     assinatura opcional
├── iniciar.pyw                lançador para Windows (janela própria, sem console)
├── perfis/padrao/             tema padrão
└── web/                       aplicação Flask (rotas, templates, estáticos)
instalador.nsi                 script NSIS do instalador para Windows
docs/instalador-windows.md     como gerar o instalador
```

## Instalador para Windows

Há um instalador NSIS que empacota um Python embutido, sem exigir Python nem administrador
na máquina de destino. Veja [docs/instalador-windows.md](docs/instalador-windows.md).
