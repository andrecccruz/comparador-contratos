/* Comparativo interativo — sem frameworks, sem CDN. Dados em #dados-resultado. */
(function () {
  'use strict';

  var R = JSON.parse(document.getElementById('dados-resultado').textContent);
  var META = R.meta || {};
  var OCS = R.ocorrencias || [];
  var TIPOS = ['PREENCHIDO', 'ALTERADO', 'INCLUIDO', 'REMOVIDO', 'MOVIDO', 'PENDENTE', 'FORMATACAO'];
  var NOMES = {PREENCHIDO: 'Preenchido', ALTERADO: 'Alterado', INCLUIDO: 'Incluído', REMOVIDO: 'Removido',
               MOVIDO: 'Movido', PENDENTE: 'Pendente', FORMATACAO: 'Formatação'};
  var ROTULOS = {
    ALTERADO: ['Modelo padrão', 'Devolução do fornecedor'],
    REMOVIDO: ['Suprimido pelo fornecedor', null],
    INCLUIDO: [null, 'Incluído pelo fornecedor'],
    MOVIDO: [null, 'Texto movido na devolução'],
    FORMATACAO: ['Modelo padrão', 'Devolução (só formatação)']
  };
  var IA = R.analise_ia || null;
  var $ = function (id) { return document.getElementById(id); };
  // dentro do sistema (pagina do resultado) o cabecalho ja aparece fora do quadro
  var EMBUTIDO = /[?&]embutido=1(&|$)/.test(location.search);
  if (EMBUTIDO) document.documentElement.classList.add('embutido');

  OCS.forEach(function (o, i) { o._n = i + 1; o._busca = normalizarBusca(
    [o.texto_modelo, o.texto_devolucao, o.clausula, NOMES[o.tipo],
     (o.campos || []).map(function (c) { return c.placeholder + ' ' + c.valor; }).join(' '),
     (o.pendentes || []).join(' ')].join(' \n ')); });
  var porId = {}, porPosDev = {}, porPosMod = {};
  OCS.forEach(function (o) {
    porId[o.id] = o;
    if (o.pos_devolucao !== '' && o.pos_devolucao != null) porPosDev[o.pos_devolucao] = o;
    if (o.pos_modelo !== '' && o.pos_modelo != null) porPosMod[o.pos_modelo] = o;
  });

  // ------------------------------------------------------------ utilitarios
  function esc(t) {
    return String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function normalizarBusca(t) {
    return String(t || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }
  function seloClasse(selo) { return selo === 'Médio' ? 'Medio' : selo; }
  function lsLer(chave) { try { return window.localStorage.getItem(chave); } catch (e) { return null; } }
  function lsGravar(chave, valor) {
    try { window.localStorage.setItem(chave, valor); return true; } catch (e) { return false; }
  }
  var avisoTimer = null;
  function avisar(texto) {
    var el = $('aviso'); el.textContent = texto; el.classList.add('ver');
    clearTimeout(avisoTimer); avisoTimer = setTimeout(function () { el.classList.remove('ver'); }, 2200);
  }
  function copiar(texto) {
    function alternativo() {
      var ta = document.createElement('textarea'); ta.value = texto; ta.setAttribute('readonly', '');
      ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select();
      var ok = false; try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      document.body.removeChild(ta); avisar(ok ? 'Copiado.' : 'Não foi possível copiar.');
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(texto).then(function () { avisar('Copiado.'); }, alternativo);
    } else { alternativo(); }
  }
  var RE_CAMPO = /\[[^\[\]\n]{1,120}\]|_{4,}(?:[\/.\-]_{2,})*/g;
  function marcarCamposModelo(texto) {
    return esc(texto).replace(RE_CAMPO, function (m) { return '<span class="ph-mod">' + m + '</span>'; });
  }
  function cssVarTipo(t) { return '--cor:var(--t-' + t + ');--bg:var(--b-' + t + ')'; }

  // ------------------------------------------------------------ revisao
  var Revisao = {
    modo: 'local',
    chaveLocal: 'revisao:' + (META.hash_entrada || (META.arquivo_devolucao + '|' + META.data_hora)),
    dados: {versao: 1, hash_entrada: META.hash_entrada || '', itens: {}},
    item: function (o) { return this.dados.itens[o.chave] || {}; },
    definir: function (o, campo, valor) {
      var it = this.dados.itens[o.chave] || {};
      if (valor === null || valor === '') delete it[campo]; else it[campo] = valor;
      it.em = new Date().toISOString();
      if (!it.status && !it.comentario) delete this.dados.itens[o.chave];
      else this.dados.itens[o.chave] = it;
      this.salvar();
    },
    carregar: function (pronto) {
      var self = this;
      var bruto = lsLer(self.chaveLocal);
      if (bruto) { try { self.dados = self.validar(JSON.parse(bruto)) || self.dados; } catch (e) { /* ignora */ } }
      if (!/^https?:$/.test(location.protocol) || !window.fetch) { pronto(); return; }
      fetch('revisao', {headers: {'Accept': 'application/json'}, credentials: 'same-origin'})
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (j && j.ok) {
            self.modo = 'servidor';
            var remoto = self.validar(j.revisao);
            if (remoto && Object.keys(remoto.itens).length) self.dados = remoto;
            else if (Object.keys(self.dados.itens).length) self.salvar();
          }
          pronto();
        }).catch(function () { pronto(); });
    },
    validar: function (d) {
      if (!d || typeof d !== 'object' || typeof d.itens !== 'object' || d.itens === null) return null;
      var limpo = {versao: 1, hash_entrada: META.hash_entrada || '', itens: {}};
      Object.keys(d.itens).forEach(function (k) {
        var it = d.itens[k] || {};
        if (!/^[0-9a-f]{6,40}$/.test(k)) return;
        var novo = {};
        if (it.status === 'aceito' || it.status === 'rejeitado') novo.status = it.status;
        if (typeof it.comentario === 'string' && it.comentario.trim()) novo.comentario = it.comentario.slice(0, 4000);
        if (typeof it.em === 'string') novo.em = it.em.slice(0, 40);
        if (typeof it.por === 'string') novo.por = it.por.slice(0, 120);
        if (novo.status || novo.comentario) limpo.itens[k] = novo;
      });
      return limpo;
    },
    _timer: null,
    salvar: function () {
      var self = this;
      self.dados.atualizado_em = new Date().toISOString();
      lsGravar(self.chaveLocal, JSON.stringify(self.dados));
      if (self.modo !== 'servidor') { mostrarModo(); return; }
      clearTimeout(self._timer);
      mostrarModo('salvando…');
      self._timer = setTimeout(function () {
        fetch('revisao', {method: 'PUT', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
          body: JSON.stringify(self.dados)})
          .then(function (r) { mostrarModo(r.ok ? null : 'falha ao salvar no servidor — mantido neste navegador'); })
          .catch(function () { mostrarModo('sem conexão — mantido neste navegador'); });
      }, 400);
    }
  };
  function mostrarModo(extra) {
    $('modo-salvar').textContent = extra ||
      (Revisao.modo === 'servidor' ? 'revisão salva no sistema' : 'revisão salva neste navegador');
  }

  // ------------------------------------------------------------ estado / filtros
  var ocultasPadrao = META.categorias_ocultas || ['FORMATACAO'];
  var estado = {
    tipos: {}, soCriticos: false, risco: [0, 100], clausula: '', status: '', busca: '',
    visao: ({inline: 'inline', lado_a_lado: 'lado', lado: 'lado', documento: 'documento'})[META.visao_padrao] || 'inline',
    atual: null
  };
  TIPOS.forEach(function (t) { estado.tipos[t] = ocultasPadrao.indexOf(t) < 0; });
  var salvoVisao = lsLer('comparativo_visao');
  if (salvoVisao && /^(inline|lado|documento)$/.test(salvoVisao)) estado.visao = salvoVisao;

  function statusRevisao(o) {
    var it = Revisao.item(o);
    return it.status || 'pendente';
  }
  function visivel(o) {
    if (!estado.tipos[o.tipo]) return false;
    if (estado.soCriticos && !o.critico) return false;
    if (o.risco < estado.risco[0] || o.risco > estado.risco[1]) return false;
    if (estado.clausula && o.clausula !== estado.clausula) return false;
    if (estado.status) {
      var it = Revisao.item(o);
      if (estado.status === 'comentado') { if (!it.comentario) return false; }
      else if (statusRevisao(o) !== estado.status) return false;
    }
    if (estado.busca && o._busca.indexOf(estado.busca) < 0) return false;
    return true;
  }
  function revisaveis() { return OCS.filter(function (o) { return !o.oculto || estado.tipos[o.tipo]; }); }

  // ------------------------------------------------------------ cabecalho e ficha
  function montarCabecalho() {
    $('titulo-doc').textContent = 'Comparativo de devolução — ' + (META.fornecedor || '');
    $('sub-meta').innerHTML = esc('Gerado em ' + META.data_hora + ' · motor v' + META.versao_motor) +
      '<br>' + esc('Modelo: ' + META.arquivo_modelo + ' · Devolução: ' + META.arquivo_devolucao);
    var r = META.risco_geral || 0, selo = META.selo_risco || 'Baixo', cls = seloClasse(selo);
    var el = $('risco-geral');
    el.setAttribute('aria-label', 'Risco geral ' + r + ' de 100, nível ' + selo);
    el.innerHTML = '<div><div class="rot">Risco geral</div><div class="valor" style="color:var(--r-' + cls + ')">' +
      r + '<small>/100</small></div></div><div><span class="chip risco-' + cls + '">' + esc(selo) + '</span>' +
      '<div class="medidor" style="margin-top:6px"><span style="width:' + r + '%;background:var(--r-' + cls +
      ')"></span></div></div>';

    var f = R.ficha || {}, partes = f.partes || {}, vig = f.vigencia || {}, ind = f.indicadores || {};
    var cnpj = META.cnpj || '';
    var cnpjInfo = (f.cnpjs || []).filter(function (c) { return c.numero === cnpj; })[0];
    var linhas = [
      ['Contratante', partes.contratante],
      ['Contratada', META.fornecedor],
      ['CNPJ', cnpj ? cnpj + (cnpjInfo ? (cnpjInfo.valido ? ' — válido' : ' — inválido') : '') : '—',
        cnpjInfo && !cnpjInfo.valido],
      ['Nº do contrato', f.numero_contrato || '—']
    ];
    if (vig.inicio || vig.meses) linhas.push(['Vigência', (vig.inicio ? vig.inicio + ' a ' + (vig.fim || '?') : '') +
      (vig.meses ? (vig.inicio ? ' (' + vig.meses + ' meses)' : vig.meses + ' meses') : ''), vig.valida === false]);
    if (ind.pagamento_dias != null) linhas.push(['Pagamento', ind.pagamento_dias + ' dias']);
    if (ind.multa_percentual_max != null) linhas.push(['Multa (maior)', String(ind.multa_percentual_max).replace('.', ',') + '%']);
    (f.valores || []).slice(0, 4).forEach(function (v) { linhas.push([v.rotulo || 'Valor', v.valor]); });
    (f.slas || []).slice(0, 3).forEach(function (s) { linhas.push(['SLA', s.linha]); });
    if (f.foro) linhas.push(['Foro', f.foro.cidade || 'não preenchido', f.foro.pendente]);
    (f.responsaveis || []).slice(0, 4).forEach(function (x) { linhas.push([x.papel, x.nome]); });
    $('ficha').innerHTML = '<h2>Ficha do contrato</h2><dl>' + linhas.map(function (l) {
      return '<dt>' + esc(l[0]) + '</dt><dd' + (l[2] ? ' class="ruim"' : '') + '>' + esc(l[1] || '—') + '</dd>';
    }).join('') + '</dl>';

    var alertas = [];
    if ((R.pendencias || []).length) {
      var nomes = {}; R.pendencias.forEach(function (p) { nomes[p.placeholder] = 1; });
      alertas.push('<li><b>Devolução incompleta:</b> ' + R.pendencias.length + ' campo(s) sem preencher — ' +
        esc(Object.keys(nomes).join(', ')) + '</li>');
    }
    (f.alertas || []).forEach(function (a) { alertas.push('<li>' + esc(a) + '</li>'); });
    var pb = R.playbook || [];
    var html = '';
    if (alertas.length) html += '<h2>Alertas</h2><ul>' + alertas.join('') + '</ul>';
    if (pb.length) {
      html += '<h2' + (alertas.length ? ' style="margin-top:12px"' : '') + '>Playbook de aceitação</h2><ul>' +
        pb.map(function (x) {
          var cls = x.status === 'ok' ? 'pb-ok' : x.status === 'fora' ? 'pb-fora' : 'pb-sem';
          var txt = x.status === 'ok' ? 'dentro' : x.status === 'fora' ? 'FORA DA POLÍTICA' : 'sem dado';
          return '<li><span class="' + cls + '">' + txt + '</span> — ' + esc(x.rotulo) +
            (x.encontrado != null ? ' (encontrado: ' + esc(x.encontrado) + ')' : '') + '</li>';
        }).join('') + '</ul>';
    }
    if (html) { $('alertas').innerHTML = html; $('alertas').hidden = false; }
    var foraPb = pb.filter(function (x) { return x.status === 'fora'; }).length;
    var partes = ['risco ' + r + '/100'];
    if (alertas.length) partes.push(alertas.length + ' alerta(s)');
    if (foraPb) partes.push(foraPb + ' fora da política');
    $('resumo-linha').textContent = '· ' + partes.join(' · ');

    if (IA) {
      $('painel-ia').hidden = false;
      $('painel-ia').innerHTML = '<h2>Análise por IA <span class="marca-ia">gerado por IA — revisar</span></h2>' +
        (IA.resumo_executivo ? '<p class="txt">' + esc(IA.resumo_executivo) + '</p>' : '') +
        (IA.checagem_playbook && IA.checagem_playbook.length ? '<ul>' + IA.checagem_playbook.map(function (c) {
          return '<li>' + esc(c) + '</li>'; }).join('') + '</ul>' : '') +
        '<p class="nota">' + esc((IA.modelo ? 'Modelo ' + IA.modelo + ' · ' : '') + (IA.gerado_em || '')) + '</p>';
    }

    $('rodape').textContent = (R.identidade && R.identidade.nome_produto ? R.identidade.nome_produto : '') +
      ' · motor v' + META.versao_motor + (R.identidade && R.identidade.rodape_relatorio ?
      ' · ' + R.identidade.rodape_relatorio : '') + ' · ' + OCS.length + ' ocorrências no total';
  }

  // ------------------------------------------------------------ contadores
  function montarContadores() {
    var cont = {}; TIPOS.forEach(function (t) { cont[t] = 0; });
    OCS.forEach(function (o) { cont[o.tipo]++; });
    var html = TIPOS.filter(function (t) { return cont[t] || t !== 'FORMATACAO'; }).map(function (t) {
      return '<button type="button" class="contador" data-tipo="' + t + '" style="--cor:var(--t-' + t + ')" ' +
        'aria-pressed="' + estado.tipos[t] + '" title="Mostrar/ocultar ' + NOMES[t] + '">' +
        '<span class="bolinha"></span><span class="nome">' + NOMES[t] + '</span><span class="n">' + cont[t] + '</span></button>';
    }).join('');
    var criticos = OCS.filter(function (o) { return o.critico; }).length;
    html += '<button type="button" class="contador crit" id="c-criticos" aria-pressed="false" title="Só críticos (c)">' +
      '<span class="bolinha"></span><span class="nome">Críticas</span><span class="n">' + criticos + '</span></button>';
    $('contadores').innerHTML = html;
  }
  function sincronizarControles() {
    Array.prototype.forEach.call(document.querySelectorAll('.contador[data-tipo]'), function (b) {
      b.setAttribute('aria-pressed', String(!!estado.tipos[b.getAttribute('data-tipo')]));
    });
    var cc = $('c-criticos'); if (cc) cc.setAttribute('aria-pressed', String(estado.soCriticos));
    $('f-criticos').setAttribute('aria-pressed', String(estado.soCriticos));
    $('f-oc-preenchido').checked = !estado.tipos.PREENCHIDO;
    $('f-oc-formatacao').checked = !estado.tipos.FORMATACAO;
    Array.prototype.forEach.call(document.querySelectorAll('.visoes button'), function (b) {
      b.setAttribute('aria-checked', String(b.getAttribute('data-visao') === estado.visao));
    });
    $('f-clausula').value = estado.clausula;
    var ativos = (estado.soCriticos ? 1 : 0) + (estado.risco[0] !== 0 || estado.risco[1] !== 100 ? 1 : 0) +
      (estado.clausula ? 1 : 0) + (estado.status ? 1 : 0) + (estado.tipos.PREENCHIDO ? 0 : 1);
    $('n-filtros').textContent = ativos; $('n-filtros').hidden = !ativos;
  }

  // ------------------------------------------------------------ sumario
  function montarSumario(lista) {
    var todas = (R.clausulas && R.clausulas.devolucao || []).map(function (c) { return c.titulo; });
    OCS.forEach(function (o) { if (todas.indexOf(o.clausula) < 0) todas.push(o.clausula); });
    var contagem = {}, riscoMax = {};
    lista.forEach(function (o) {
      contagem[o.clausula] = (contagem[o.clausula] || 0) + 1;
      riscoMax[o.clausula] = Math.max(riscoMax[o.clausula] || 0, o.risco);
    });
    var maior = Math.max.apply(null, [1].concat(Object.keys(contagem).map(function (k) { return contagem[k]; })));
    $('lista-clausulas').innerHTML = todas.map(function (c) {
      var n = contagem[c] || 0, r = riscoMax[c] || 0;
      var cor = r >= 60 ? 'var(--r-Alto)' : r >= 30 ? 'var(--r-Medio)' : n ? 'var(--r-Baixo)' : 'var(--borda)';
      return '<li' + (n ? '' : ' class="vazia"') + '><button type="button" data-clausula="' + esc(c) + '"' +
        (estado.clausula === c ? ' aria-current="true"' : '') + ' title="' + esc(c) + '">' +
        '<span class="nome">' + esc(c) + '</span><span class="calor"><b style="width:' +
        Math.round(4 + 100 * n / maior) + 'px;--cor:' + cor + '"></b>' + n + (r ? ' · risco ' + r : '') +
        '</span></button></li>';
    }).join('');
    var total = Math.max(1, META.qtd_devolucao || 1);
    var ids = {}; lista.forEach(function (o) { ids[o.id] = 1; });
    $('minimapa').innerHTML = OCS.map(function (o) {
      var pos = o.pos_devolucao !== '' ? o.pos_devolucao : (o.pos_modelo || 0);
      var topo = Math.min(97, Math.max(1, 100 * (pos - 1) / total));
      return '<i data-ir="' + o.id + '" title="#' + o._n + ' ' + NOMES[o.tipo] + ' · ' + esc(o.clausula) + '" ' +
        'class="' + (ids[o.id] ? '' : 'fora') + '" style="top:' + topo + '%;background:var(--t-' + o.tipo + ')"></i>';
    }).join('');
    var sel = $('f-clausula'), atual = estado.clausula;
    sel.innerHTML = '<option value="">todas</option>' + todas.map(function (c) {
      return '<option value="' + esc(c) + '">' + esc(c.length > 60 ? c.slice(0, 58) + '…' : c) + '</option>';
    }).join('');
    sel.value = atual;
  }

  // ------------------------------------------------------------ cartoes (visao lista)
  function corpoOcorrencia(o) {
    var h = '';
    if (o.tipo === 'PREENCHIDO' || o.tipo === 'PENDENTE') {
      if ((o.campos || []).length || (o.pendentes || []).length) {
        h += '<div class="rot">Campos</div><div class="campos">' + (o.campos || []).map(function (c) {
          return '<span class="ph">' + esc(c.placeholder) + '</span><span class="v">' + esc(c.valor) + '</span>';
        }).join('') + (o.pendentes || []).map(function (p) {
          return '<span class="ph">' + esc(p) + '</span><span class="pend">sem preencher</span>';
        }).join('') + '</div>';
      }
      h += '<div class="rot">Texto na devolução</div><p class="txt">' + esc(o.texto_devolucao) + '</p>';
    } else {
      var r = ROTULOS[o.tipo] || ['Modelo', 'Devolução'];
      if (r[0]) h += '<div class="rot">' + r[0] + '</div><p class="txt">' + o.html_modelo + '</p>';
      if (r[1]) h += '<div class="rot">' + r[1] + '</div><p class="txt">' + o.html_devolucao + '</p>';
    }
    return h;
  }
  function cabecalhoOcorrencia(o) {
    var it = Revisao.item(o);
    var h = '<a class="num" href="#' + o.id + '" title="Link para esta ocorrência">#' + o._n + '</a>' +
      '<span class="chip tipo">' + NOMES[o.tipo] + '</span>' +
      '<span class="chip risco-' + seloClasse(o.selo) + '" title="Risco ' + o.risco + ' de 100">' + esc(o.selo) + ' · ' + o.risco + '</span>';
    if (o.critico) h += '<span class="chip crit">Crítica</span>';
    if (o.tambem_movido) h += '<span class="chip mov">também movido</span>';
    (o.fora_politica || []).forEach(function (fp) { h += '<span class="chip fp">Fora da política: ' + esc(fp) + '</span>'; });
    if (o.aprovado_antes) h += '<span class="chip st-aceito" title="Biblioteca de cláusulas aprovadas">Já aprovado antes em ' +
      esc(o.aprovado_antes.data) + (o.aprovado_antes.fornecedor ? ' / ' + esc(o.aprovado_antes.fornecedor) : '') + '</span>';
    if (it.status) h += '<span class="chip st-' + it.status + '">' + (it.status === 'aceito' ? 'Aceita' : 'Rejeitada') + '</span>';
    h += '<span class="clausula">' + esc(o.clausula) + '</span><span>modelo ' + (o.pos_modelo || '—') +
      ' · devolução ' + (o.pos_devolucao || '—') + '</span>';
    return h;
  }
  function extrasOcorrencia(o) {
    var it = Revisao.item(o), h = '';
    if ((o.fatores || []).length && o.tipo !== 'FORMATACAO') {
      h += '<details class="fatores"><summary>Por que risco ' + o.risco + '?</summary><ul>' +
        o.fatores.map(function (f) { return '<li>' + esc(f) + '</li>'; }).join('') + '</ul></details>';
    }
    var ia = IA && IA.ocorrencias && IA.ocorrencias[o.chave];
    if (ia) {
      h += '<div class="ia-oc"><b>IA (revisar):</b> ' + esc(ia.explicacao || '') +
        (ia.sugestao ? '<br><b>Sugestão:</b> ' + esc(ia.sugestao) : '') + '</div>';
    }
    if (it.comentario) h += '<div class="comentario"><b>Comentário</b>' + esc(it.comentario) + '</div>';
    h += '<div class="acoes" role="group" aria-label="Revisão da ocorrência ' + o._n + '">' +
      '<button type="button" class="aceitar" data-acao="aceitar" aria-pressed="' + (it.status === 'aceito') + '">✓ Aceitar</button>' +
      '<button type="button" class="rejeitar" data-acao="rejeitar" aria-pressed="' + (it.status === 'rejeitado') + '">✕ Rejeitar</button>' +
      '<button type="button" data-acao="comentar">✎ Comentar</button>' +
      '<button type="button" data-acao="copiar" title="Copiar o trecho da devolução">Copiar trecho</button>' +
      '<button type="button" data-acao="link" title="Copiar link direto">Copiar link</button></div>';
    return h;
  }
  function cartao(o) {
    var it = Revisao.item(o);
    return '<article class="oc' + (it.status ? ' st-' + it.status : '') + (estado.atual === o.id ? ' atual' : '') +
      '" id="' + o.id + '" data-id="' + o.id + '" tabindex="-1" style="' + cssVarTipo(o.tipo) + '" ' +
      'aria-label="Ocorrência ' + o._n + ', ' + NOMES[o.tipo] + ', risco ' + esc(o.selo) + '">' +
      '<header>' + cabecalhoOcorrencia(o) + '</header>' + corpoOcorrencia(o) + extrasOcorrencia(o) + '</article>';
  }
  function renderLista(lista) {
    if (!lista.length) return '<div class="vazio">Nenhuma ocorrência com os filtros atuais.</div>';
    return lista.map(cartao).join('');
  }

  // ------------------------------------------------------------ visao lado a lado
  function textoDoc(lado, pos) {
    var d = R.documento && R.documento[lado];
    if (!d || pos == null) return null;
    return d[pos - 1] || null;
  }
  function renderLado(lista) {
    if (!R.alinhamento || !R.documento) return '<div class="vazio">Este resultado não traz o texto integral ' +
      '(opção “incluir texto integral” desligada).</div>';
    var vis = {}; lista.forEach(function (o) { vis[o.id] = 1; });
    var linhas = [], iguais = [];
    function descarregar() {
      if (!iguais.length) return;
      if (iguais.length <= 3) { linhas.push(iguais.join('')); }
      else {
        linhas.push(iguais.slice(0, 1).join('') + '<button type="button" class="recolhido" data-expandir>' +
          '▸ ' + (iguais.length - 2) + ' parágrafos sem alteração</button><div hidden>' +
          iguais.slice(1, -1).join('') + '</div>' + iguais.slice(-1).join(''));
      }
      iguais = [];
    }
    R.alinhamento.forEach(function (par) {
      var a = textoDoc('modelo', par[0]), b = textoDoc('devolucao', par[1]);
      var o = (par[1] != null && porPosDev[par[1]]) || (par[0] != null && porPosMod[par[0]] &&
              porPosMod[par[0]].pos_devolucao === '' ? porPosMod[par[0]] : null);
      if (o && (par[1] == null || o.pos_devolucao === par[1]) && vis[o.id]) {
        descarregar();
        var ha = o.tipo === 'MOVIDO' ? esc(o.texto_modelo) : (o.html_modelo || '');
        var hb = o.html_devolucao || '';
        if (o.tipo === 'PREENCHIDO' || o.tipo === 'PENDENTE') { ha = marcarCamposModelo(o.texto_modelo); hb = destacarCampos(o); }
        linhas.push('<div class="lado-linha com-oc' + (estado.atual === o.id ? ' atual' : '') + (b && b.titulo ? ' titulo' : '') +
          '" id="lado-' + o.id + '" data-id="' + o.id + '" style="' + cssVarTipo(o.tipo) + '">' +
          '<div class="marcador"><a href="#' + o.id + '" title="' + NOMES[o.tipo] + ' · risco ' + o.risco + '">#' + o._n + '</a></div>' +
          '<div class="a">' + (a ? ha || esc(a.texto) : '<span class="vazio-lado">—</span>') + '</div>' +
          '<div class="b">' + (b ? hb || esc(b.texto) : '<span class="vazio-lado">— suprimido —</span>') + '</div></div>');
      } else {
        iguais.push('<div class="lado-linha' + ((b || a || {}).titulo ? ' titulo' : '') + '"><div class="marcador"></div>' +
          '<div class="a">' + (a ? esc(a.texto) : '') + '</div><div class="b">' + (b ? esc(b.texto) : '') + '</div></div>');
      }
    });
    descarregar();
    return '<div class="lado"><div class="lado-cab"><div></div><div>Modelo padrão</div><div>Devolução</div></div>' +
      linhas.join('') + '</div>';
  }

  // ------------------------------------------------------------ visao documento
  function renderDocumento(lista) {
    if (!R.alinhamento || !R.documento) return '<div class="vazio">Este resultado não traz o texto integral.</div>';
    var vis = {}; lista.forEach(function (o) { vis[o.id] = 1; });
    var h = [];
    R.alinhamento.forEach(function (par) {
      var b = textoDoc('devolucao', par[1]);
      if (b) {
        var o = porPosDev[par[1]];
        if (o && vis[o.id]) {
          var corpo = (o.tipo === 'PREENCHIDO' || o.tipo === 'PENDENTE') ? destacarCampos(o) : o.html_devolucao;
          h.push('<p class="marcado' + (b.titulo ? ' titulo' : '') + (estado.atual === o.id ? ' atual' : '') +
            '" id="doc-' + o.id + '" data-id="' + o.id + '" style="' + cssVarTipo(o.tipo) + '">' +
            '<a class="tag-oc" href="#' + o.id + '">#' + o._n + ' ' + NOMES[o.tipo] + '</a>' + corpo + '</p>');
        } else {
          h.push('<p' + (b.titulo ? ' class="titulo"' : '') + '>' + esc(b.texto) + '</p>');
        }
      } else {
        var a = textoDoc('modelo', par[0]);
        var r = porPosMod[par[0]];
        if (a && r && vis[r.id]) {
          h.push('<p class="marcado removido-bloco' + (estado.atual === r.id ? ' atual' : '') + '" id="doc-' + r.id +
            '" data-id="' + r.id + '" style="' + cssVarTipo(r.tipo) + '"><a class="tag-oc" href="#' + r.id + '">#' +
            r._n + ' ' + NOMES[r.tipo] + '</a><span class="removido">' + esc(a.texto) + '</span></p>');
        }
      }
    });
    return '<div class="documento">' + h.join('') + '</div>';
  }
  function destacarCampos(o) {
    var t = esc(o.texto_devolucao);
    (o.campos || []).forEach(function (c) {
      var v = esc(c.valor);
      if (v && v.length > 1) t = t.split(v).join('<span class="val-ok" title="' + esc(c.placeholder) + '">' + v + '</span>');
    });
    if ((o.pendentes || []).length) {
      t = t.replace(RE_CAMPO, function (m) { return '<span class="ph-pend" title="sem preencher">' + m + '</span>'; });
    }
    return t;
  }

  // ------------------------------------------------------------ busca com destaque
  function destacarBusca(raiz) {
    if (!estado.busca) return;
    var alvos = raiz.querySelectorAll('.txt, .campos .v, .lado-linha>div.a, .lado-linha>div.b, .documento p');
    var termo = estado.busca;
    Array.prototype.forEach.call(alvos, function (el) {
      var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, false), nos = [], n;
      while ((n = walker.nextNode())) nos.push(n);
      nos.forEach(function (no) {
        var texto = no.nodeValue, norm = normalizarBusca(texto), i = norm.indexOf(termo);
        if (i < 0 || norm.length !== texto.length) {
          if (i < 0) return;
        }
        var frag = document.createDocumentFragment(), ultimo = 0;
        while (i >= 0) {
          frag.appendChild(document.createTextNode(texto.slice(ultimo, i)));
          var m = document.createElement('mark'); m.textContent = texto.slice(i, i + termo.length);
          frag.appendChild(m); ultimo = i + termo.length; i = norm.indexOf(termo, ultimo);
        }
        frag.appendChild(document.createTextNode(texto.slice(ultimo)));
        no.parentNode.replaceChild(frag, no);
      });
    });
  }

  // ------------------------------------------------------------ render principal
  var ultimaLista = [];
  function aplicar(opcoes) {
    opcoes = opcoes || {};
    var lista = OCS.filter(visivel);
    ultimaLista = lista;
    sincronizarControles();
    montarSumario(lista);
    var alvo = $('lista');
    var html = estado.visao === 'lado' ? renderLado(lista) : estado.visao === 'documento' ? renderDocumento(lista)
      : renderLista(lista);
    alvo.innerHTML = html;
    destacarBusca(alvo);
    atualizarProgresso();
    alvo.setAttribute('aria-label', lista.length + ' ocorrências exibidas');
    if (opcoes.rolarPara) irPara(opcoes.rolarPara, false);
  }
  function atualizarProgresso() {
    var base = revisaveis();
    var feitas = base.filter(function (o) { return !!Revisao.item(o).status; }).length;
    var total = base.length;
    $('progresso-texto').textContent = feitas + ' de ' + total + ' revisadas · exibindo ' + ultimaLista.length +
      ' de ' + OCS.length + ' ocorrências';
    var barra = $('progresso-barra');
    barra.setAttribute('aria-valuemax', String(total)); barra.setAttribute('aria-valuenow', String(feitas));
    $('progresso-valor').style.width = (total ? Math.round(100 * feitas / total) : 0) + '%';
  }
  function elementoDe(id) {
    return document.getElementById(estado.visao === 'lado' ? 'lado-' + id : estado.visao === 'documento' ? 'doc-' + id : id);
  }
  function irPara(id, foco) {
    var el = elementoDe(id);
    if (!el) {                                      // filtrado: limpa filtros e tenta de novo
      var o = porId[id]; if (!o) return;
      estado.tipos[o.tipo] = true; estado.soCriticos = false; estado.clausula = ''; estado.status = '';
      estado.busca = ''; $('f-busca').value = ''; estado.risco = [0, 100]; $('f-risco').value = '0-100';
      aplicar(); el = elementoDe(id); if (!el) return;
    }
    var anterior = document.querySelector('.atual'); if (anterior) anterior.classList.remove('atual');
    estado.atual = id; el.classList.add('atual');
    var escondido = el.closest && el.closest('[hidden]'); if (escondido) escondido.hidden = false;
    el.scrollIntoView({block: 'center', behavior: 'smooth'});
    if (foco !== false && el.focus) { el.setAttribute('tabindex', '-1'); el.focus({preventScroll: true}); }
    if (history.replaceState) history.replaceState(null, '', '#' + id);
  }
  function mover(delta) {
    if (!ultimaLista.length) return;
    var idx = -1;
    for (var i = 0; i < ultimaLista.length; i++) if (ultimaLista[i].id === estado.atual) idx = i;
    idx = idx < 0 ? (delta > 0 ? 0 : ultimaLista.length - 1) : Math.max(0, Math.min(ultimaLista.length - 1, idx + delta));
    irPara(ultimaLista[idx].id);
  }

  // ------------------------------------------------------------ acoes de revisao
  function acaoOcorrencia(id, acao) {
    var o = porId[id]; if (!o) return;
    estado.atual = id;
    if (acao === 'aceitar' || acao === 'rejeitar') {
      var novo = acao === 'aceitar' ? 'aceito' : 'rejeitado';
      Revisao.definir(o, 'status', Revisao.item(o).status === novo ? null : novo);
      avisar('#' + o._n + (Revisao.item(o).status ? (novo === 'aceito' ? ' aceita' : ' rejeitada') : ' sem decisão'));
      atualizarOcorrencia(o);
    } else if (acao === 'comentar') {
      abrirComentario(o);
    } else if (acao === 'copiar') {
      copiar(o.texto_devolucao || o.texto_modelo);
    } else if (acao === 'link') {
      copiar(location.href.split('#')[0] + '#' + o.id);
    }
  }
  function atualizarOcorrencia(o) {
    if (estado.visao === 'inline' && document.getElementById(o.id) && visivel(o)) {
      var el = document.getElementById(o.id);
      var tmp = document.createElement('div'); tmp.innerHTML = cartao(o);
      el.parentNode.replaceChild(tmp.firstChild, el);
      destacarBusca(document.getElementById(o.id));
      atualizarProgresso();
    } else { aplicar(); }
  }
  function abrirComentario(o) {
    if (estado.visao !== 'inline') { estado.visao = 'inline'; aplicar(); }
    var el = document.getElementById(o.id); if (!el) { irPara(o.id); el = document.getElementById(o.id); if (!el) return; }
    if (el.querySelector('.caixa-comentario')) { el.querySelector('textarea').focus(); return; }
    var caixa = document.createElement('div'); caixa.className = 'caixa-comentario';
    caixa.innerHTML = '<label class="rotulo-oculto" for="com-' + o.id + '">Comentário</label>' +
      '<textarea id="com-' + o.id + '" maxlength="4000" placeholder="Comentário sobre esta ocorrência"></textarea>' +
      '<div><button type="button" data-acao-com="salvar">Salvar comentário</button>' +
      '<button type="button" class="secundario" data-acao-com="cancelar">Cancelar</button></div>';
    el.appendChild(caixa);
    var ta = caixa.querySelector('textarea'); ta.value = Revisao.item(o).comentario || ''; ta.focus();
  }

  // ------------------------------------------------------------ exportar / importar
  function exportar() {
    var pacote = {tipo: 'revisao-comparativo', versao: 1, hash_entrada: META.hash_entrada || '',
      fornecedor: META.fornecedor, arquivo_devolucao: META.arquivo_devolucao, exportado_em: new Date().toISOString(),
      itens: Revisao.dados.itens,
      resumo: OCS.map(function (o) {
        var it = Revisao.item(o);
        return {id: o.id, chave: o.chave, tipo: o.tipo, clausula: o.clausula, status: it.status || 'pendente',
                comentario: it.comentario || ''};
      })};
    var blob = new Blob([JSON.stringify(pacote, null, 1)], {type: 'application/json'});
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'revisao_' + String(META.fornecedor || 'contrato').replace(/[^\w\-]+/g, '_').slice(0, 40) + '.json';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    avisar('Revisão exportada.');
  }
  function importar(arquivo) {
    if (!arquivo) return;
    if (arquivo.size > 5 * 1024 * 1024) { avisar('Arquivo grande demais.'); return; }
    var leitor = new FileReader();
    leitor.onload = function () {
      var d; try { d = JSON.parse(leitor.result); } catch (e) { avisar('Arquivo inválido.'); return; }
      var limpo = Revisao.validar(d);
      if (!limpo) { avisar('Arquivo inválido.'); return; }
      if (d.hash_entrada && META.hash_entrada && d.hash_entrada !== META.hash_entrada &&
          !window.confirm('Esta revisão foi feita para outra comparação. Importar mesmo assim? ' +
                          '(só as ocorrências com o mesmo texto serão aplicadas)')) return;
      var conhecidas = {}; OCS.forEach(function (o) { conhecidas[o.chave] = 1; });
      var n = 0;
      Object.keys(limpo.itens).forEach(function (k) {
        if (conhecidas[k]) { Revisao.dados.itens[k] = limpo.itens[k]; n++; }
      });
      Revisao.salvar(); aplicar(); avisar(n + ' decisão(ões) importada(s).');
    };
    leitor.readAsText(arquivo);
  }

  // ------------------------------------------------------------ tema
  function alternarTema() {
    var novo = document.documentElement.getAttribute('data-tema') === 'escuro' ? 'claro' : 'escuro';
    document.documentElement.setAttribute('data-tema', novo);
    lsGravar('contratos_tema', novo);
  }

  // ------------------------------------------------------------ eventos
  function ligarEventos() {
    $('contadores').addEventListener('click', function (e) {
      var b = e.target.closest('.contador'); if (!b) return;
      if (b.id === 'c-criticos') estado.soCriticos = !estado.soCriticos;
      else { var t = b.getAttribute('data-tipo'); estado.tipos[t] = !estado.tipos[t]; }
      aplicar();
    });
    $('f-criticos').addEventListener('click', function () { estado.soCriticos = !estado.soCriticos; aplicar(); });
    $('f-risco').addEventListener('change', function () {
      var p = this.value.split('-'); estado.risco = [+p[0], +p[1]]; aplicar();
    });
    $('f-clausula').addEventListener('change', function () { estado.clausula = this.value; aplicar(); });
    $('f-status').addEventListener('change', function () { estado.status = this.value; aplicar(); });
    $('f-oc-preenchido').addEventListener('change', function () { estado.tipos.PREENCHIDO = !this.checked; aplicar(); });
    $('f-oc-formatacao').addEventListener('change', function () { estado.tipos.FORMATACAO = !this.checked; aplicar(); });
    var buscaTimer = null;
    $('f-busca').addEventListener('input', function () {
      var v = this.value; clearTimeout(buscaTimer);
      buscaTimer = setTimeout(function () { estado.busca = normalizarBusca(v.trim()); aplicar(); }, 160);
    });
    document.querySelector('.visoes').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-visao]'); if (!b) return;
      trocarVisao(b.getAttribute('data-visao'));
    });
    document.addEventListener('click', function (e) {
      var b = e.target.closest('[data-pop]');
      if (b) {
        var pop = $(b.getAttribute('data-pop')), abrir = pop.hidden;
        fecharPops(); pop.hidden = !abrir; b.setAttribute('aria-expanded', String(abrir));
        return;
      }
      if (!e.target.closest('.pop') || e.target.closest('.pop-menu [role=menuitem]')) fecharPops();
    });
    $('b-limpar-filtros').addEventListener('click', function () {
      estado.soCriticos = false; estado.risco = [0, 100]; $('f-risco').value = '0-100';
      estado.clausula = ''; estado.status = ''; $('f-status').value = '';
      TIPOS.forEach(function (t) { estado.tipos[t] = ocultasPadrao.indexOf(t) < 0; });
      aplicar(); fecharPops();
    });
    $('b-resumo').addEventListener('click', function () { mostrarResumo($('painel-superior').hidden, true); });
    $('b-tema').addEventListener('click', alternarTema);
    $('b-imprimir').addEventListener('click', function () { window.print(); });
    $('b-ajuda').addEventListener('click', abrirAjuda);
    $('b-fechar-ajuda').addEventListener('click', fecharAjuda);
    $('dialogo-ajuda').addEventListener('click', function (e) { if (e.target === this) fecharAjuda(); });
    $('b-exportar').addEventListener('click', exportar);
    $('b-importar').addEventListener('change', function () { importar(this.files[0]); this.value = ''; });
    $('b-sumario').addEventListener('click', function () {
      var s = $('sumario'), aberto = !s.classList.contains('aberto');
      s.classList.toggle('aberto', aberto); this.setAttribute('aria-expanded', String(aberto));
    });
    $('lista-clausulas').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-clausula]'); if (!b) return;
      var c = b.getAttribute('data-clausula');
      estado.clausula = estado.clausula === c ? '' : c; aplicar();
      $('lista').focus({preventScroll: true}); window.scrollTo({top: $('barra').offsetTop, behavior: 'smooth'});
    });
    $('minimapa').addEventListener('click', function (e) {
      var i = e.target.closest('i[data-ir]'); if (i) irPara(i.getAttribute('data-ir'));
    });
    $('lista').addEventListener('click', function (e) {
      var exp = e.target.closest('[data-expandir]');
      if (exp) { exp.nextElementSibling.hidden = false; exp.remove(); return; }
      var bc = e.target.closest('[data-acao-com]');
      if (bc) {
        var art = bc.closest('.oc'), o = porId[art.getAttribute('data-id')];
        if (bc.getAttribute('data-acao-com') === 'salvar') {
          Revisao.definir(o, 'comentario', art.querySelector('textarea').value.trim().slice(0, 4000));
          avisar('Comentário salvo.');
        }
        atualizarOcorrencia(o); return;
      }
      var b = e.target.closest('button[data-acao]');
      if (b) { acaoOcorrencia(b.closest('[data-id]').getAttribute('data-id'), b.getAttribute('data-acao')); return; }
      var link = e.target.closest('a[href^="#oc-"]');
      if (link) { e.preventDefault(); irPara(link.getAttribute('href').slice(1)); return; }
      var linha = e.target.closest('[data-id]');
      if (linha && !e.target.closest('a,button,textarea')) {
        var anterior = document.querySelector('.atual'); if (anterior) anterior.classList.remove('atual');
        estado.atual = linha.getAttribute('data-id'); linha.classList.add('atual');
      }
    });
    document.addEventListener('keydown', function (e) {
      var alvo = e.target, digitando = /^(INPUT|TEXTAREA|SELECT)$/.test(alvo.tagName);
      if (e.key === 'Escape') {
        if (!$('dialogo-ajuda').hidden) { fecharAjuda(); return; }
        if (document.querySelector('.pop:not([hidden])')) { fecharPops(); return; }
        if (digitando) { alvo.blur(); return; }
      }
      if (digitando || e.ctrlKey || e.metaKey || e.altKey) return;
      var k = e.key;
      if (k === 'j') { e.preventDefault(); mover(1); }
      else if (k === 'k') { e.preventDefault(); mover(-1); }
      else if (k === 'c') { estado.soCriticos = !estado.soCriticos; aplicar(); avisar(estado.soCriticos ? 'Só críticas' : 'Todas'); }
      else if (k === '/') { e.preventDefault(); $('f-busca').focus(); $('f-busca').select(); }
      else if (k === '?') { e.preventDefault(); abrirAjuda(); }
      else if (k === '1' || k === '2' || k === '3') { trocarVisao(['inline', 'lado', 'documento'][+k - 1]); }
      else if ((k === 'a' || k === 'x' || k === 'm') && estado.atual) {
        e.preventDefault(); acaoOcorrencia(estado.atual, k === 'a' ? 'aceitar' : k === 'x' ? 'rejeitar' : 'comentar');
      }
    });
    window.addEventListener('hashchange', function () {
      var id = location.hash.slice(1); if (porId[id] && id !== estado.atual) irPara(id);
    });
  }
  function fecharPops() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-pop]'), function (b) {
      $(b.getAttribute('data-pop')).hidden = true; b.setAttribute('aria-expanded', 'false');
    });
  }
  function mostrarResumo(aberto, lembrar) {
    $('painel-superior').hidden = !aberto;
    $('b-resumo').setAttribute('aria-expanded', String(aberto));
    if (lembrar) lsGravar('comparativo_ficha' + (EMBUTIDO ? '_sistema' : ''), aberto ? '1' : '0');
    medirBarra();
  }
  function trocarVisao(v) {
    estado.visao = v; lsGravar('comparativo_visao', v);
    aplicar({rolarPara: estado.atual});
  }
  var focoAntesAjuda = null;
  function abrirAjuda() { focoAntesAjuda = document.activeElement; $('dialogo-ajuda').hidden = false; $('b-fechar-ajuda').focus(); }
  function fecharAjuda() { $('dialogo-ajuda').hidden = true; if (focoAntesAjuda && focoAntesAjuda.focus) focoAntesAjuda.focus(); }

  // altura da barra fixa: usada nos deslocamentos de rolagem e cabecalhos fixos
  function medirBarra() {
    var barra = $('barra'), fixa = getComputedStyle(barra).position === 'sticky';
    document.documentElement.style.setProperty('--altura-barra', (fixa ? barra.offsetHeight : 0) + 'px');
  }
  window.addEventListener('resize', medirBarra);
  if (window.ResizeObserver) new ResizeObserver(medirBarra).observe($('barra'));

  // ------------------------------------------------------------ inicio
  montarCabecalho();
  // ficha/alertas: recolhidos por padrao dentro do sistema; lembra a escolha
  var fichaSalva = lsLer('comparativo_ficha' + (EMBUTIDO ? '_sistema' : ''));
  mostrarResumo(fichaSalva ? fichaSalva === '1' : !EMBUTIDO);
  medirBarra();
  montarContadores();
  ligarEventos();
  Revisao.carregar(function () {
    mostrarModo();
    aplicar();
    var id = location.hash.slice(1);
    if (porId[id]) setTimeout(function () { irPara(id); }, 60);
  });
  window.__comparativo = {estado: estado, revisao: Revisao, aplicar: aplicar, irPara: irPara};
})();
