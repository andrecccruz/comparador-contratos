/* painel.js — painel de ações reutilizado por Comparar, Histórico e Modelos,
   e controle do tema claro/escuro. */

(function () {
  const TEMA_CHAVE = 'contratos_tema';

  window.aplicarTema = function (tema) {
    document.documentElement.setAttribute('data-tema', tema);
    try { localStorage.setItem(TEMA_CHAVE, tema); } catch (e) { /* modo privado */ }
    const alternador = document.getElementById('troca-tema');
    if (alternador) alternador.checked = (tema === 'escuro');
    const rotulo = document.getElementById('rotulo-tema');
    if (rotulo) rotulo.textContent = (tema === 'escuro') ? 'Escuro' : 'Claro';
  };

  window.temaAtual = function () {
    return document.documentElement.getAttribute('data-tema') || 'claro';
  };

  window.alternarTema = function () {
    window.aplicarTema(window.temaAtual() === 'escuro' ? 'claro' : 'escuro');
  };
})();

/* ------------------------------------------------------------------ painel */

let painelDownloads = [];

/**
 * config = {
 *   titulo: string,
 *   visualizarUrl: string | null,   // conteúdo do iframe
 *   downloads: [{rotulo, url}],     // opções da caixa de seleção
 *   extraHtml: string | null,       // bloco livre acima do conteúdo
 *   mensagemVazia: string | null    // quando não há o que visualizar
 * }
 */
function abrirPainel(config) {
  const painel = document.getElementById('painel');
  if (!painel) return;

  document.getElementById('painel-titulo').textContent = config.titulo || '';

  const extra = document.getElementById('painel-extra');
  if (config.extraHtml) {
    extra.innerHTML = config.extraHtml;
    extra.style.display = '';
  } else {
    extra.innerHTML = '';
    extra.style.display = 'none';
  }

  painelDownloads = config.downloads || [];
  const barra = document.getElementById('painel-barra');
  const seletor = document.getElementById('painel-formato');
  if (painelDownloads.length) {
    seletor.innerHTML = painelDownloads
      .map((d, i) => '<option value="' + i + '">' + d.rotulo + '</option>').join('');
    barra.style.display = '';
  } else {
    barra.style.display = 'none';
  }

  const quadro = document.getElementById('painel-frame');
  const vazio = document.getElementById('painel-vazio');
  if (config.visualizarUrl) {
    quadro.src = config.visualizarUrl;
    quadro.style.display = '';
    vazio.style.display = 'none';
  } else {
    quadro.src = 'about:blank';
    quadro.style.display = 'none';
    vazio.textContent = config.mensagemVazia || 'Nada para visualizar.';
    vazio.style.display = 'flex';
  }

  painel.classList.add('aberto');
}

function fecharPainel() {
  const painel = document.getElementById('painel');
  if (!painel) return;
  painel.classList.remove('aberto');
  document.getElementById('painel-frame').src = 'about:blank';
}

function baixarSelecionado() {
  const seletor = document.getElementById('painel-formato');
  const escolhido = painelDownloads[parseInt(seletor.value, 10)];
  if (escolhido) window.location.href = escolhido.url;
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') fecharPainel();
});

/* ------------------------------------------------ ações de um resultado */

function escaparHtml(t) {
  return String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

var STATUS_REVISAO = {pendente: 'Pendente', em_revisao: 'Em revisão', aprovado: 'Aprovado',
                      devolvido: 'Devolvido'};

/** Bloco de ações do painel: status, responsável, redline, parecer, rodadas. */
function acoesResultadoHtml(id, info) {
  info = info || {};
  var opcoes = Object.keys(STATUS_REVISAO).map(function (k) {
    return '<option value="' + k + '"' + (info.status_revisao === k ? ' selected' : '') + '>' +
      STATUS_REVISAO[k] + '</option>';
  }).join('');
  var rodadas = (info.rodadas_total > 1)
    ? '<a href="/rodadas/' + id + '"><button type="button" class="secundario pequeno">Rodadas (v' +
      info.rodada + ' de ' + info.rodadas_total + ')</button></a>' : '';
  return '<div class="linha acoes-resultado" style="gap:10px;align-items:flex-end">' +
    '<div class="campo curto"><label for="ar-status">Status da revisão</label>' +
    '<select id="ar-status">' + opcoes + '</select></div>' +
    '<div class="campo curto"><label for="ar-resp">Responsável</label>' +
    '<input type="text" id="ar-resp" maxlength="120" value="' + escaparHtml(info.responsavel || '') + '"></div>' +
    '<button type="button" class="pequeno" onclick="salvarStatusRevisao(\'' + id + '\')">Salvar</button>' +
    '<span style="flex:1"></span>' +
    '<button type="button" class="secundario pequeno" onclick="gerarRedline(\'' + id + '\')" ' +
    'title="Contraproposta .docx com controle de alterações: rejeitadas voltam ao texto do modelo">Contraproposta (.docx)</button>' +
    '<button type="button" class="secundario pequeno" onclick="gerarParecer(\'' + id + '\')">Parecer (PDF/DOCX)</button>' +
    rodadas + (window.IA_ATIVA ? '<button type="button" class="secundario pequeno" onclick="gerarAnaliseIA(\'' + id +
      '\')" title="Envia trechos (anonimizados, se configurado) para a API de IA autorizada">Análise por IA</button>' : '') +
    '</div><div id="ar-msg" class="dica" role="status" style="margin-top:6px"></div>';
}

function _postJSON(url, dados) {
  return fetch(url, {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
    body: JSON.stringify(dados || {})}).then(function (r) {
      return r.json().catch(function () { return {ok: false, erro: 'Falha (' + r.status + ').'}; });
    });
}
function _msg(texto, erro) {
  var el = document.getElementById('ar-msg');
  if (el) { el.textContent = texto; el.style.color = erro ? 'var(--erro)' : ''; }
}
function salvarStatusRevisao(id) {
  _postJSON('/api/execucoes/' + id + '/status-revisao', {
    status_revisao: document.getElementById('ar-status').value,
    responsavel: document.getElementById('ar-resp').value
  }).then(function (r) {
    _msg(r.ok ? 'Status salvo: ' + STATUS_REVISAO[r.status_revisao] : (r.erro || 'Falha.'), !r.ok);
    var linha = document.querySelector('tr[data-id="' + id + '"] .st-rev');
    if (r.ok && linha) linha.textContent = STATUS_REVISAO[r.status_revisao];
  });
}
function gerarRedline(id) {
  _msg('Gerando contraproposta…');
  _postJSON('/resultado/' + id + '/redline').then(function (r) {
    if (!r.ok) { _msg(r.erro || 'Falha ao gerar.', true); return; }
    var s = r.resumo;
    _msg('Contraproposta gerada: ' + s.revertidas + ' revertida(s), ' + s.reinseridas +
         ' reinserida(s), ' + s.excluidas + ' excluída(s), ' + s.comentarios + ' comentário(s). Baixando…');
    window.location.href = r.url;
  });
}
function gerarParecer(id) {
  var obs = window.prompt('Observações para o parecer (opcional):', '') ;
  if (obs === null) return;
  _msg('Gerando parecer…');
  _postJSON('/resultado/' + id + '/parecer', {observacao: obs}).then(function (r) {
    if (!r.ok) { _msg(r.erro || 'Falha ao gerar.', true); return; }
    _msg('Parecer gerado. ');
    var el = document.getElementById('ar-msg');
    el.innerHTML = 'Parecer gerado: <a href="' + r.pdf + '">PDF</a> · <a href="' + r.docx + '">DOCX</a>';
  });
}
function downloadsResultado(id) {
  return [
    {rotulo: 'PDF (comparativo)', url: '/resultado/' + id + '/baixar/pdf'},
    {rotulo: 'HTML interativo', url: '/resultado/' + id + '/baixar/html'},
    {rotulo: 'Excel (alterações)', url: '/resultado/' + id + '/baixar/xlsx'},
    {rotulo: 'JSON (resultado)', url: '/resultado/' + id + '/baixar/json'},
    {rotulo: 'TXT (resumo)', url: '/resultado/' + id + '/baixar/txt'},
    {rotulo: 'Contraproposta (.docx, após gerar)', url: '/resultado/' + id + '/baixar/redline'},
    {rotulo: 'Parecer PDF (após gerar)', url: '/resultado/' + id + '/baixar/parecer_pdf'},
    {rotulo: 'Tudo (.zip)', url: '/resultado/' + id + '/zip'}
  ];
}

function gerarAnaliseIA(id) {
  if (!confirm('Enviar os trechos críticos desta comparação para a API de IA autorizada?')) return;
  _msg('Consultando a IA… (pode levar até um minuto)');
  _postJSON('/resultado/' + id + '/ia').then(function (r) {
    if (!r.ok) { _msg(r.erro || 'Falha.', true); return; }
    _msg('Análise por IA pronta (' + r.ocorrencias + ' trechos comentados) — gerado por IA, revise.');
    var quadro = document.getElementById('painel-frame');
    if (quadro && quadro.src) quadro.src = quadro.src;
  });
}
