/* ajustes.js — envio dos formulários da tela Ajustes como JSON (fetch), com
   erros exibidos ao lado de cada campo e prévia ao vivo da identidade. */
(function () {
  'use strict';
  var aviso = document.getElementById('aviso-ajustes');

  function mostrar(texto, tipo) {
    aviso.textContent = texto;
    aviso.className = 'aviso-ajustes ' + (tipo || 'ok');
    aviso.hidden = false;
    aviso.scrollIntoView({block: 'nearest'});
  }
  function limparErros(form) {
    Array.prototype.forEach.call(form.querySelectorAll('[data-erro]'), function (e) { e.textContent = ''; });
    Array.prototype.forEach.call(form.querySelectorAll('[aria-invalid]'), function (e) { e.removeAttribute('aria-invalid'); });
  }
  function exibirErros(form, erros) {
    var sobras = [];
    Object.keys(erros || {}).forEach(function (chave) {
      var alvo = form.querySelector('[data-erro="' + chave + '"]') ||
                 form.querySelector('[data-erro="' + chave.split('.').pop() + '"]');
      var m = /^(\w+)\.(\d+)\.(\w+)$/.exec(chave);
      if (m) {
        var linha = form.querySelectorAll('[data-item]')[+m[2]];
        var campo = linha && linha.querySelector('[data-campo="' + m[3] + '"]');
        if (campo) { campo.setAttribute('aria-invalid', 'true'); campo.title = erros[chave]; }
      }
      if (alvo) alvo.textContent = erros[chave]; else sobras.push(erros[chave]);
    });
    mostrar('Não foi salvo. ' + (sobras.length ? sobras.join(' ') : 'Confira os campos destacados.'), 'erro');
  }
  function enviarJSON(url, dados) {
    return fetch(url, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
      body: JSON.stringify(dados)}).then(function (r) {
        return r.json().catch(function () { return {ok: false, erros: {_: 'Resposta inválida (' + r.status + ').'}}; });
      });
  }
  function valorCampo(el) {
    if (el.type === 'checkbox') return el.checked;
    if (el.multiple) return Array.prototype.filter.call(el.options, function (o) { return o.selected; })
      .map(function (o) { return o.value; });
    return el.value;
  }

  // formulário comum: name="a" ou name="valores.b"; checkboxes com value viram lista
  function serializar(form) {
    var dados = {};
    var extra = form.getAttribute('data-extra');
    if (extra) { try { Object.assign(dados, JSON.parse(extra)); } catch (e) { /* ignora */ } }
    var lista = form.getAttribute('data-lista');
    if (lista) {
      dados[lista] = Array.prototype.map.call(form.querySelectorAll('[data-item]'), function (linha) {
        var item = {};
        Array.prototype.forEach.call(linha.querySelectorAll('[data-campo]'), function (el) {
          item[el.getAttribute('data-campo')] = valorCampo(el);
        });
        return item;
      });
      return dados;
    }
    var multiplos = {};
    Array.prototype.forEach.call(form.querySelectorAll('input[type=checkbox][name]'), function (el) {
      multiplos[el.name] = (multiplos[el.name] || 0) + 1;
    });
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.disabled || el.type === 'file' || el.type === 'submit') return;
      var alvo = dados, nome = el.name;
      if (nome.indexOf('valores.') === 0) { dados.valores = dados.valores || {}; alvo = dados.valores; nome = nome.slice(8); }
      if (el.type === 'checkbox') {
        var lista = multiplos[el.name] > 1 || ['herdar', 'funcoes', 'extensoes_aceitas', 'categorias_ocultas'].indexOf(nome) >= 0;
        if (lista) { alvo[nome] = alvo[nome] || []; if (el.checked) alvo[nome].push(el.value); }
        else alvo[nome] = el.checked;
      } else { alvo[nome] = el.value; }
    });
    return dados;
  }

  Array.prototype.forEach.call(document.querySelectorAll('form[data-secao], form[data-url]'), function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      limparErros(form);
      var botao = form.querySelector('button[type=submit]'); if (botao) botao.disabled = true;
      var url = form.getAttribute('data-url') || ('/api/ajustes/' + form.getAttribute('data-secao'));
      enviarJSON(url, serializar(form))
        .then(function (r) {
          if (r.ok) {
            var depois = form.getAttribute('data-apos-salvar');
            mostrar((r.mensagem || 'Salvo.') + (depois ? ' Agora envie o contrato-modelo…' : ''));
            setTimeout(function () { if (depois) location.href = depois; else location.reload(); }, depois ? 1200 : 700);
          }
          else exibirErros(form, r.erros);
        })
        .catch(function (err) { mostrar('Falha de conexão: ' + err, 'erro'); })
        .then(function () { if (botao) botao.disabled = false; });
    });
  });

  // uploads (logo, chave privada)
  Array.prototype.forEach.call(document.querySelectorAll('form[data-upload]'), function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault(); limparErros(form);
      fetch(form.getAttribute('data-upload'), {method: 'POST', body: new FormData(form),
        credentials: 'same-origin', headers: {'X-Requested-With': 'fetch'}})
        .then(function (r) { return r.json(); })
        .then(function (r) {
          if (r.ok) { mostrar(r.mensagem || 'Enviado.'); setTimeout(function () { location.reload(); }, 700); }
          else exibirErros(form, r.erros);
        }).catch(function (err) { mostrar('Falha: ' + err, 'erro'); });
    });
  });

  // linhas dinâmicas (departamentos, playbook)
  document.addEventListener('click', function (e) {
    var add = e.target.closest('[data-adicionar]');
    if (add) {
      var tpl = document.getElementById(add.getAttribute('data-adicionar'));
      var destino = document.getElementById(add.getAttribute('data-destino'));
      destino.appendChild(tpl.content.cloneNode(true));
      var campo = destino.lastElementChild.querySelector('input[type=text]'); if (campo) campo.focus();
      return;
    }
    var rem = e.target.closest('[data-remover]');
    if (rem) {
      if (rem.getAttribute('data-confirmar') && !confirm(rem.getAttribute('data-confirmar'))) return;
      rem.closest('[data-item]').remove();
      var pend = document.getElementById('dep-pendente'); if (pend) pend.hidden = false;
      return;
    }
    var restDep = e.target.closest('[data-restaurar-dep]');
    if (restDep) {
      var d = JSON.parse(restDep.getAttribute('data-restaurar-dep'));
      var tplDep = document.getElementById('modelo-linha-dep');
      var corpo = document.getElementById('linhas-dep');
      if (!tplDep || !corpo) return;
      corpo.appendChild(tplDep.content.cloneNode(true));
      var nova = corpo.lastElementChild;
      var cod = nova.querySelector('[data-campo=codigo]'); cod.value = d.codigo; cod.readOnly = true;
      nova.querySelector('[data-campo=nome]').value = d.nome || '';
      nova.querySelector('[data-campo=cor]').value = d.cor || '#8b929b';
      nova.querySelector('[data-campo=icone]').value = d.icone || 'documento';
      nova.querySelector('[data-campo=ativo]').checked = true;
      restDep.closest('tr').remove();
      mostrar('Departamento ' + d.codigo + ' de volta na lista — clique em Salvar departamentos.');
      return;
    }
    var perfil = e.target.closest('[data-perfil]');
    if (perfil && !perfil.classList.contains('ativo')) {
      perfil.disabled = true;
      enviarJSON('/api/ajustes/perfil', {perfil: perfil.getAttribute('data-perfil')}).then(function (r) {
        if (r.ok) { mostrar(r.mensagem); setTimeout(function () { location.reload(); }, 500); }
        else { perfil.disabled = false; mostrar('Não foi possível trocar: ' + JSON.stringify(r.erros || {}), 'erro'); }
      });
      return;
    }
    var restPerfil = e.target.closest('[data-restaurar-perfil]');
    if (restPerfil) {
      if (!confirm('Descartar a personalização e voltar ao padrão deste perfil?')) return;
      enviarJSON('/api/ajustes/perfil', {perfil: restPerfil.getAttribute('data-restaurar-perfil'), restaurar: true})
        .then(function (r) {
          if (r.ok) { mostrar(r.mensagem); setTimeout(function () { location.reload(); }, 500); }
          else mostrar('Não foi possível restaurar.', 'erro');
        });
      return;
    }
    var rest = e.target.closest('[data-restaurar]');
    if (rest) {
      if (!confirm('Restaurar a configuração de ' + rest.getAttribute('data-restaurar') + '? A atual vira um novo backup.')) return;
      enviarJSON('/api/ajustes/restaurar', {arquivo: rest.getAttribute('data-restaurar')}).then(function (r) {
        if (r.ok) { mostrar(r.mensagem); setTimeout(function () { location.reload(); }, 700); }
        else mostrar('Não foi possível restaurar: ' + JSON.stringify(r.erros || {}), 'erro');
      });
    }
  });

  // exemplos do playbook
  var exemplos = document.getElementById('b-exemplos-pb');
  if (exemplos) exemplos.addEventListener('click', function () {
    var modelo = [['Multa até 10%', 'multa_percentual_max', '<=', '10'],
                  ['SLA crítico até 4 h', 'sla_horas_min', '<=', '4'],
                  ['Pagamento em 30 dias ou mais', 'pagamento_dias', '>=', '30'],
                  ['Vigência até 24 meses', 'vigencia_meses', '<=', '24']];
    var tpl = document.getElementById('modelo-linha-pb'), destino = document.getElementById('linhas-pb');
    modelo.forEach(function (m) {
      destino.appendChild(tpl.content.cloneNode(true));
      var linha = destino.lastElementChild;
      linha.querySelector('[data-campo=rotulo]').value = m[0];
      linha.querySelector('[data-campo=campo]').value = m[1];
      linha.querySelector('[data-campo=operador]').value = m[2];
      linha.querySelector('[data-campo=valor]').value = m[3];
    });
    mostrar('Exemplos incluídos — revise e clique em Salvar playbook.');
  });

  // prévia ao vivo da identidade
  var cor = document.getElementById('i-cor'), picker = document.getElementById('i-cor-picker');
  if (cor && picker) {
    var previa = document.getElementById('previa-identidade');
    var timer = null;
    function atualizarPrevia(valor) {
      if (!/^#[0-9a-fA-F]{6}$/.test(valor)) return;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fetch('/api/ajustes/tokens?cor=' + encodeURIComponent(valor)).then(function (r) { return r.json(); })
          .then(function (t) {
            if (!t.ok) return;
            var tema = document.documentElement.getAttribute('data-tema') === 'escuro' ? t.escuro : t.claro;
            Object.keys(tema).forEach(function (k) { previa.style.setProperty('--' + k, tema[k]); });
            document.getElementById('avisos-contraste').innerHTML = t.avisos.map(function (a) {
              return '<li>' + a.replace(/</g, '&lt;') + '</li>'; }).join('') ||
              '<li class="ok">Contraste AA ok (botão ' + t.contraste.botao_texto_branco + ':1, texto ' +
              t.contraste.texto_forte_fundo_claro + ':1).</li>';
          });
      }, 120);
    }
    picker.addEventListener('input', function () { cor.value = picker.value.toUpperCase(); atualizarPrevia(cor.value); });
    cor.addEventListener('input', function () { if (/^#[0-9a-fA-F]{6}$/.test(cor.value)) picker.value = cor.value; atualizarPrevia(cor.value); });
    ['i-nome', 'i-curto'].forEach(function (id) {
      document.getElementById(id).addEventListener('input', function () {
        document.getElementById(id === 'i-nome' ? 'previa-produto' : 'previa-curto').textContent = this.value;
      });
    });
    atualizarPrevia(cor.value);
  }

  // herdar: desabilita o campo correspondente
  Array.prototype.forEach.call(document.querySelectorAll('.herdar input'), function (chk) {
    var campo = chk.closest('.campo');
    function aplicar() {
      Array.prototype.forEach.call(campo.querySelectorAll('input, textarea, select'), function (el) {
        if (el.closest('.herdar') || el.getAttribute('name') === 'herdar') return;
        el.disabled = chk.checked;
      });
    }
    chk.addEventListener('change', aplicar); aplicar();
  });

  // retenção
  var simular = document.getElementById('b-simular'), executar = document.getElementById('b-executar');
  var saida = document.getElementById('resultado-retencao');
  function tabelaRetencao(r) {
    if (!r.itens.length && !r.mantidos.length) return '<p class="dica">Nada anterior a ' + (r.limite || '—') + '.</p>';
    var linhas = r.itens.map(function (i) {
      return '<tr><td>' + i.data + '</td><td>' + i.tipo + '</td><td><code>' + i.caminho.replace(/</g, '&lt;') +
        '</code></td><td class="num">' + (i.bytes / 1024).toFixed(1) + ' KB</td></tr>';
    }).concat(r.mantidos.map(function (i) {
      return '<tr><td>' + i.data + '</td><td>' + i.tipo + '</td><td><code>' + i.caminho.replace(/</g, '&lt;') +
        '</code></td><td>mantido: ' + i.motivo + '</td></tr>';
    }));
    return '<p class="dica">' + r.itens.length + ' item(ns) anteriores a ' + r.limite + ' — ' +
      (r.bytes / 1048576).toFixed(2) + ' MB.</p><table><thead><tr><th>Data</th><th>Tipo</th><th>Caminho</th><th></th></tr></thead><tbody>' +
      linhas.join('') + '</tbody></table>';
  }
  if (simular) simular.addEventListener('click', function () {
    enviarJSON('/api/ajustes/retencao/simular', {}).then(function (r) {
      saida.innerHTML = tabelaRetencao(r); executar.disabled = !r.itens.length;
    });
  });
  if (executar) executar.addEventListener('click', function () {
    if (!confirm('Mover os itens listados para dados/_removidos?')) return;
    enviarJSON('/api/ajustes/retencao/executar', {}).then(function (r) {
      saida.innerHTML = '<p>' + r.movidos + ' item(ns) movido(s) para <code>' + (r.destino || '') + '</code>.</p>';
      executar.disabled = true;
    });
  });

  // testar DocuSign
  var testar = document.getElementById('b-testar-ds');
  if (testar) testar.addEventListener('click', function () {
    var alvo = document.getElementById('resultado-teste-ds'); alvo.textContent = 'testando…';
    enviarJSON('/api/ajustes/assinatura/testar', {departamento: testar.getAttribute('data-dep')})
      .then(function (r) { alvo.textContent = r.mensagem; alvo.style.color = r.ok ? 'var(--ok)' : 'var(--erro)'; });
  });

  // IA: liga/desliga imediato
  var iaAtivo = document.getElementById('ia-ativo');
  if (iaAtivo) iaAtivo.addEventListener('change', function () {
    var alvo = document.getElementById('erro-ia-ativo'); alvo.textContent = '';
    iaAtivo.disabled = true;
    enviarJSON('/api/ajustes/ia', {ativo: iaAtivo.checked}).then(function (r) {
      if (r.ok) { mostrar(iaAtivo.checked ? 'IA ligada.' : 'IA desligada.'); setTimeout(function () { location.reload(); }, 500); }
      else { iaAtivo.checked = !iaAtivo.checked; iaAtivo.disabled = false;
             alvo.textContent = (r.erros && (r.erros.ativo || r.erros._ || r.erros.ia)) || 'Não foi possível alterar.'; }
    });
  });
  // IA: campos do provedor escolhido
  var iaProv = document.getElementById('ia-prov');
  function mostrarProvedor() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-prov]'), function (el) {
      el.hidden = el.getAttribute('data-prov') !== iaProv.value;
    });
  }
  if (iaProv) { iaProv.addEventListener('change', mostrarProvedor); mostrarProvedor(); }
  var testarIA = document.getElementById('b-testar-ia');
  if (testarIA) testarIA.addEventListener('click', function () {
    var alvo = document.getElementById('resultado-teste-ia');
    alvo.textContent = 'testando… (salve a conexão antes, se mudou algo)'; alvo.style.color = '';
    testarIA.disabled = true;
    enviarJSON('/api/ajustes/ia_testar', {}).then(function (r) {
      alvo.textContent = r.mensagem || 'Falha.'; alvo.style.color = r.ok ? 'var(--ok)' : 'var(--erro)';
    }).catch(function (err) { alvo.textContent = 'Falha: ' + err; })
      .then(function () { testarIA.disabled = false; });
  });

  // importar configuração
  var imp = document.getElementById('imp-config');
  if (imp) imp.addEventListener('change', function () {
    var arq = this.files[0]; if (!arq) return;
    var leitor = new FileReader();
    leitor.onload = function () {
      var dados; try { dados = JSON.parse(leitor.result); } catch (e) { mostrar('Arquivo não é JSON.', 'erro'); return; }
      enviarJSON('/api/ajustes/importar', dados).then(function (r) {
        if (r.ok) { mostrar(r.mensagem); setTimeout(function () { location.reload(); }, 800); }
        else mostrar('Importação recusada: ' + Object.keys(r.erros || {}).map(function (k) { return r.erros[k]; }).join(' '), 'erro');
      });
    };
    leitor.readAsText(arq); this.value = '';
  });

  if (window.aplicarTema && window.temaAtual) window.aplicarTema(window.temaAtual());
})();
