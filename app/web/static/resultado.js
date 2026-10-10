/* resultado.js — página cheia de um resultado: menus suspensos, status da revisão,
   assinatura e painel lateral de IA (análise sob demanda + assistente com filtro). */
(function () {
  'use strict';
  var ID = window.RESULTADO_ID;
  var $ = function (id) { return document.getElementById(id); };
  var esc = window.escaparHtml;

  // ------------------------------------------------------------ menus suspensos
  function fecharMenus(exceto) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-suspenso]'), function (b) {
      var pop = $(b.getAttribute('data-suspenso'));
      if (pop && pop !== exceto) { pop.hidden = true; b.setAttribute('aria-expanded', 'false'); }
    });
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-suspenso]');
    if (b) {
      var pop = $(b.getAttribute('data-suspenso'));
      fecharMenus(pop);
      pop.hidden = !pop.hidden;
      b.setAttribute('aria-expanded', String(!pop.hidden));
      if (!pop.hidden) { var foco = pop.querySelector('select, input, button, a'); if (foco) foco.focus(); }
      return;
    }
    if (!e.target.closest('.pop')) fecharMenus();
    else if (e.target.closest('.pop-menu [role=menuitem]')) fecharMenus();
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') fecharMenus(); });

  // ------------------------------------------------------------ status da revisão
  var salvar = $('b-salvar-status');
  if (salvar) salvar.addEventListener('click', function () {
    window.salvarStatusRevisao(ID);
    var sel = $('ar-status');
    $('rotulo-status').textContent = sel.options[sel.selectedIndex].text;
    fecharMenus();
  });

  // ------------------------------------------------------------ assinatura
  window.abrirAssinatura = function () {
    $('lista-signatarios').innerHTML = ''; $('ass-aviso').textContent = '';
    window.adicionarSignatario();
    $('modal-assinatura').classList.add('aberto');
  };
  window.fecharAssinatura = function () { $('modal-assinatura').classList.remove('aberto'); };
  window.adicionarSignatario = function () {
    var linha = document.createElement('div');
    linha.className = 'linha'; linha.style.marginTop = '8px';
    linha.innerHTML =
      '<div class="campo" style="min-width:160px"><label>Nome</label><input type="text" class="ass-nome"></div>' +
      '<div class="campo" style="min-width:200px"><label>E-mail</label><input type="text" class="ass-email"></div>' +
      '<div class="campo curto" style="min-width:80px"><label>Ordem</label><input type="text" class="ass-ordem" value="1" style="width:60px"></div>' +
      '<button type="button" class="secundario pequeno" onclick="this.parentElement.remove()">Remover</button>';
    $('lista-signatarios').appendChild(linha);
  };
  window.enviarAssinatura = function () {
    var aviso = $('ass-aviso'); aviso.textContent = '';
    var signatarios = Array.prototype.map.call(document.querySelectorAll('#lista-signatarios .linha'), function (l) {
      return {nome: l.querySelector('.ass-nome').value.trim(), email: l.querySelector('.ass-email').value.trim(),
              ordem: parseInt(l.querySelector('.ass-ordem').value, 10) || 1};
    }).filter(function (s) { return s.nome && s.email; });
    if (!signatarios.length) { aviso.textContent = 'Informe nome e e-mail de ao menos um signatário.'; return; }
    var botao = $('btn-enviar-assinatura'); botao.disabled = true; botao.textContent = 'Enviando...';
    fetch('/resultado/' + ID + '/assinatura/enviar', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({signatarios: signatarios})})
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (par) {
        if (!par[0]) { aviso.textContent = par[1].erro || 'Falha ao enviar.'; return; }
        window.fecharAssinatura(); location.reload();
      })
      .catch(function (err) { aviso.textContent = 'Erro de conexão: ' + err; })
      .then(function () { botao.disabled = false; botao.textContent = 'Enviar'; });
  };

  // ------------------------------------------------------------ painel de IA
  var painel = $('painel-ia');
  if (!painel) return;
  var abrir = $('b-abrir-ia'), carregado = false;

  function alternarPainel(aberto) {
    painel.hidden = !aberto;
    abrir.setAttribute('aria-expanded', String(aberto));
    abrir.classList.toggle('ativo', aberto);
    $('res-corpo').classList.toggle('com-ia', aberto);
    if (aberto && !carregado) { carregado = true; carregarAnalise(); carregarChat(); }
    if (aberto) { var foco = painel.querySelector('[aria-selected=true]'); if (foco) foco.focus(); }
  }
  abrir.addEventListener('click', function () { alternarPainel(painel.hidden); });
  $('b-fechar-ia').addEventListener('click', function () { alternarPainel(false); abrir.focus(); });

  painel.querySelector('.pia-abas').addEventListener('click', function (e) {
    var b = e.target.closest('[data-aba-ia]'); if (!b) return;
    Array.prototype.forEach.call(painel.querySelectorAll('[data-aba-ia]'), function (x) {
      x.setAttribute('aria-selected', String(x === b));
    });
    Array.prototype.forEach.call(painel.querySelectorAll('[data-secao-ia]'), function (s) {
      s.hidden = s.getAttribute('data-secao-ia') !== b.getAttribute('data-aba-ia');
    });
    if (b.getAttribute('data-aba-ia') === 'chat' && $('chat-pergunta')) $('chat-pergunta').focus();
  });

  function postJSON(url, dados) {
    return fetch(url, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
      body: JSON.stringify(dados || {})}).then(function (r) {
        return r.json().catch(function () { return {ok: false, erro: 'Falha (' + r.status + ').'}; });
      });
  }

  // ---- análise completa
  function renderAnalise(a, resumo) {
    var alvo = $('ia-analise-conteudo'); if (!alvo) return;
    var h = '';
    if (resumo && (resumo.texto || (resumo.bullets || []).length)) {
      h += '<h3>Resumo rápido</h3>' + (resumo.texto ? '<p>' + esc(resumo.texto) + '</p>' : '') +
        '<ul>' + (resumo.bullets || []).map(function (b) { return '<li>' + esc(b) + '</li>'; }).join('') + '</ul>';
    }
    if (a) {
      h += '<h3>Análise completa <span class="marca-ia">gerado por IA — revisar</span></h3>' +
        (a.resumo_executivo ? '<p>' + esc(a.resumo_executivo) + '</p>' : '') +
        ((a.checagem_playbook || []).length ? '<h4>Playbook</h4><ul>' + a.checagem_playbook.map(function (c) {
          return '<li>' + esc(c) + '</li>'; }).join('') + '</ul>' : '') +
        '<p class="dica">' + Object.keys(a.ocorrencias || {}).length + ' trecho(s) comentado(s) nos cartões · ' +
        esc(a.modelo || '') + ' · ' + esc(a.gerado_em || '') + '</p>';
    }
    if (!h) h = '<p class="dica">A IA só analisa quando você pedir — nada é enviado ao abrir esta página.</p>';
    alvo.innerHTML = h;
  }
  var analiseAtual = null, resumoAtual = null;
  function carregarAnalise() {
    if (!$('ia-analise-conteudo')) return;
    Promise.all([
      fetch('/resultado/' + ID + '/ia', {credentials: 'same-origin'}).then(function (r) { return r.json(); }),
      fetch('/resultado/' + ID + '/ia/resumo', {credentials: 'same-origin'}).then(function (r) { return r.json(); })
    ]).then(function (res) {
      analiseAtual = res[0].analise; resumoAtual = res[1].resumo;
      renderAnalise(analiseAtual, resumoAtual);
    }).catch(function () { /* sem análise salva */ });
  }
  var analisar = $('b-analisar');
  if (analisar) analisar.addEventListener('click', function () {
    if (analiseAtual && !confirm('Refazer a análise gasta tokens de novo. Continuar?')) return;
    analisar.disabled = true; var texto = analisar.textContent; analisar.textContent = 'Analisando… (até 1 min)';
    postJSON('/resultado/' + ID + '/ia').then(function (r) {
      if (!r.ok) { window._msg(r.erro || 'Falha na análise.', true); return; }
      analiseAtual = r.analise; renderAnalise(analiseAtual, resumoAtual);
      window._msg('Análise pronta: ' + r.ocorrencias + ' trecho(s) comentado(s) nos cartões.');
      var quadro = $('res-frame'); quadro.src = quadro.src;
      texto = 'Refazer análise';
    }).catch(function (err) { window._msg('Falha: ' + err, true); })
      .then(function () { analisar.disabled = false; analisar.textContent = texto; });
  });

  // ---- assistente (chat)
  var log = $('chat-log'), form = $('chat-form'), campo = $('chat-pergunta');
  var maxCaracteres = 500, restantes = null;
  function formatarResposta(t) {
    // markdown mínimo: **negrito**, listas com - ou •, quebras de linha
    var linhas = esc(t).split(/\n/), h = '', emLista = false;
    linhas.forEach(function (l) {
      var m = /^\s*(?:[-•*]|\d+[.)])\s+(.*)$/.exec(l);
      if (m) { if (!emLista) { h += '<ul>'; emLista = true; } h += '<li>' + m[1] + '</li>'; return; }
      if (emLista) { h += '</ul>'; emLista = false; }
      if (l.trim()) h += '<p>' + l + '</p>';
    });
    if (emLista) h += '</ul>';
    return h.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  }
  function adicionar(papel, texto, extra) {
    var div = document.createElement('div');
    div.className = 'msg msg-' + papel + (extra && extra.classe ? ' ' + extra.classe : '');
    div.innerHTML = (papel === 'ia' ? formatarResposta(texto) : '<p>' + esc(texto) + '</p>') +
      (extra && extra.nota ? '<small>' + esc(extra.nota) + '</small>' : '');
    log.appendChild(div); log.scrollTop = log.scrollHeight;
    return div;
  }
  function atualizarContador() {
    var n = campo.value.length;
    $('chat-contador').textContent = n + '/' + maxCaracteres +
      (restantes != null ? ' · ' + restantes + ' pergunta(s) hoje' : '');
    $('chat-contador').style.color = n > maxCaracteres ? 'var(--erro)' : '';
  }
  function carregarChat() {
    if (!log) return;
    fetch('/resultado/' + ID + '/ia/chat', {credentials: 'same-origin'}).then(function (r) { return r.json(); })
      .then(function (j) {
        maxCaracteres = j.max_caracteres || 500; campo.maxLength = maxCaracteres; restantes = j.restantes_hoje;
        log.innerHTML = '';
        if (!(j.mensagens || []).length) {
          adicionar('sistema', 'Pergunte sobre este contrato: alterações, riscos, campos pendentes, termos jurídicos. ' +
            'Exemplos abaixo.');
        }
        (j.mensagens || []).forEach(function (m) {
          adicionar(m.papel === 'usuario' ? 'usuario' : 'ia', m.texto, m.cache && m.papel === 'ia' ? {nota: 'resposta reaproveitada'} : null);
        });
        $('chat-exemplos').open = !(j.mensagens || []).length;
        $('chat-sugestoes').innerHTML = (j.sugestoes || []).map(function (s) {
          return '<button type="button" class="sugestao" data-sugestao="' + esc(s) + '">' + esc(s) + '</button>';
        }).join('');
        atualizarContador();
      });
  }
  if (form) {
    campo.addEventListener('input', atualizarContador);
    campo.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (form.requestSubmit) form.requestSubmit(); else form.dispatchEvent(new Event('submit', {cancelable: true}));
      }
    });
    $('chat-sugestoes').addEventListener('click', function (e) {
      var b = e.target.closest('[data-sugestao]'); if (!b) return;
      campo.value = b.getAttribute('data-sugestao'); atualizarContador(); campo.focus();
      $('chat-exemplos').open = false;
    });
    $('b-limpar-chat').addEventListener('click', function () {
      if (!confirm('Limpar a conversa deste contrato? (fica guardada no registro)')) return;
      postJSON('/resultado/' + ID + '/ia/chat/limpar').then(function () { carregarChat(); });
    });
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var pergunta = campo.value.trim();
      if (!pergunta) { campo.focus(); return; }
      if (pergunta.length > maxCaracteres) { adicionar('sistema', 'Pergunta longa demais (máximo ' + maxCaracteres + ').', {classe: 'bloqueada'}); return; }
      var enviar = $('b-enviar-chat'); enviar.disabled = true; campo.disabled = true;
      var minha = adicionar('usuario', pergunta);
      var espera = adicionar('ia', '…', {classe: 'pensando'});
      postJSON('/resultado/' + ID + '/ia/chat', {pergunta: pergunta}).then(function (r) {
        espera.remove();
        if (r.ok) {
          adicionar('ia', r.resposta, r.cache ? {nota: 'resposta reaproveitada — sem custo'} : null);
          campo.value = '';
          if (r.restantes_hoje != null) restantes = r.restantes_hoje;
        } else if (r.bloqueada) {
          minha.classList.add('barrada');
          adicionar('sistema', r.erro, {classe: 'bloqueada', nota: 'não enviada à IA — sem custo'});
        } else {
          adicionar('sistema', r.erro || 'Falha ao consultar a IA.', {classe: 'bloqueada'});
        }
      }).catch(function (err) { espera.remove(); adicionar('sistema', 'Falha de conexão: ' + err, {classe: 'bloqueada'}); })
        .then(function () { enviar.disabled = false; campo.disabled = false; campo.focus(); atualizarContador(); });
    });
  }
})();
