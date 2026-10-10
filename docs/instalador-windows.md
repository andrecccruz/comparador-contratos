# Instalador para Windows

O instalador empacota um Python embutido e as dependências, então a máquina de destino não
precisa ter Python nem permissão de administrador.

## Como o app funciona instalado

- `app/iniciar.pyw` roda com o `pythonw.exe` embutido, sem janela de console.
- O Flask é servido por `waitress`, só em `127.0.0.1`, na porta 8765 (ou na próxima livre).
- Abre no Microsoft Edge em modo aplicativo (`--app=`); sem Edge, no navegador padrão.
- Se o app já estiver aberto, um novo clique só abre outra janela.
- O servidor se encerra sozinho depois de um período sem janelas abertas e sem comparação
  em andamento.
- Programa em `%LOCALAPPDATA%\Programs\Comparador de Contratos\`.
- Dados em `%LOCALAPPDATA%\Comparador de Contratos\` (`dados\`, `config_web.json`,
  `ia_config\`, `logs\`), preservados ao reinstalar e ao desinstalar.

## Estrutura do pacote

```
Comparador de Contratos\
├── runtime\   Python 3.12 embeddable oficial + Lib\site-packages
│              python312._pth inclui ..\app e ..\app\web
├── app\       código + icone.ico
└── LEIA-ME.txt
```

## Gerar o instalador (Linux/WSL)

```bash
# 1) runtime: Python embeddable 3.12 (o pacote PyPI python-embed traz o zip oficial)
pip download python-embed==3.12.0 --no-deps --no-binary :all: -d /tmp/pe
#    extrair src3.12/python_embed/data.zip -> cp312/* para stage/runtime/

# 2) bibliotecas para Windows
pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 \
  --implementation cp -d wheels flask "python-docx>=1.2" openpyxl "xhtml2pdf>=0.2.11" \
  pdfplumber pypdf waitress colorama tzdata
pip install --no-deps --no-index --target stage/runtime/Lib/site-packages \
  --platform win_amd64 --python-version 3.12 --implementation cp --only-binary=:all: wheels/*.whl

#    para reduzir o tamanho: remova __pycache__, lxml/includes, *.pyi, *.h, tests/,
#    setuptools e _distutils_hack (o Python recompila na primeira abertura)

# 3) stage/runtime/python312._pth (CRLF):
#    python312.zip / . / Lib\site-packages / ..\app / ..\app\web / import site

# 4) código em stage/app, LEIA-ME.txt em stage/, e então:
makensis instalador.nsi     # -> Instalar_Comparador_Contratos.exe
```

No Windows dá para fazer o mesmo com o NSIS instalado (`makensis.exe`); os passos 1–3 viram
cópias de pasta.

A biblioteca do DocuSign não entra no pacote por padrão (aumenta bastante o instalador); a
aba de assinatura eletrônica some quando ela não está instalada.
