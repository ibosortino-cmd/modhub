(() => {
  const T = new URLSearchParams(location.search).get('t') || '';
  const $ = (s) => document.querySelector(s);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const ICONS = {
    play: '<path d="M8 5v14l11-7z" fill="currentColor" stroke="none"/>',
    check: '<path d="m20 6-11 11-5-5"/>',
    download: '<path d="M12 4v11m0 0 4-4m-4 4-4-4M5 20h14"/>',
    up: '<path d="M12 20V9m0 0-4 4m4-4 4 4M5 4h14"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    warn: '<path d="M12 3 2 20h20z"/><path d="M12 10v4m0 3v.01"/>',
    shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="M12 8v4m0 3v.01"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    trash: '<path d="M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    box: '<path d="m3 8 9-5 9 5v8l-9 5-9-5z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8v.01"/>',
    link: '<path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
    volume: '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>',
    monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    sparkles: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
    layout: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/>',
    gamepad: '<rect x="2" y="7" width="20" height="11" rx="5"/><path d="M7 10v5M4.5 12.5h5M16 11.5v.01M18.5 14v.01"/>',
    sliders: '<path d="M4 6h8M18 6h2M4 12h2M12 12h8M4 18h10M20 18h0"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    library: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    save: '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  };
  const ic = (n) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n]}</svg>`;

  const EXEC = new Set(['.dll', '.exe', '.so', '.cmd', '.bat', '.ps1', '.sh', '.py', '.lnk']);
  const S = { data: null, gameId: null, tab: 'all', q: '', openId: null, detail: null, version: null,
              profiles: {}, lastJob: null, polling: false, animate: true, refreshing: false,
              view: 'library', set: null, qe: '', updates: {}, hw: null, optLevel: null, found: {}, setTarget: null, lastNotice: undefined };

  // ---------------------------------------------------------------- helpers
  async function api(name, body) {
    const url = `/api/${name}${name.includes('?') ? '&' : '?'}t=${encodeURIComponent(T)}`;
    const res = await fetch(url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    let data = {};
    try { data = await res.json(); } catch { /* empty body */ }
    if (!res.ok) { const e = new Error(data.error || `Errore ${res.status}`); e.status = res.status; throw e; }
    return data;
  }
  const vparts = (v) => (String(v).match(/\d+/g) || ['0']).map(Number);
  function cmpv(a, b) {
    const x = vparts(a), y = vparts(b);
    for (let i = 0; i < Math.max(x.length, y.length); i++) { const d = (x[i] || 0) - (y[i] || 0); if (d) return d; }
    return 0;
  }
  const GAME_HUE = 145; // games are shown in green
  const hue = (id) => { let h = 0; for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
  const initials = (name) => name.replace(/\(.*?\)/g, '').trim().split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';
  const bytes = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
  const game = () => S.data && S.data.games.find((g) => g.id === S.gameId);
  const projects = () => (S.data ? S.data.projects.filter((p) => p.game === S.gameId) : []);
  const project = (id) => S.data && S.data.projects.find((p) => p.id === id);
  const busy = () => !!(S.data && S.data.job && S.data.job.state === 'running');

  function toast(text, kind = 'ok') {
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.innerHTML = `${ic(kind === 'error' ? 'warn' : 'check')}<span>${esc(text)}</span>`;
    $('#toasts').append(el);
    setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, kind === 'error' ? 6500 : 3800);
  }

  function modal({ icon = 'info', title, text = '', extra = '', ok = 'OK', cancel = 'Annulla', danger = false, onShow }) {
    return new Promise((resolve) => {
      const root = $('#modal');
      root.innerHTML = `<div class="modal" role="dialog" aria-modal="true">
        <div class="m-icon">${ic(icon)}</div><h3>${esc(title)}</h3>${text ? `<p>${text}</p>` : ''}${extra}
        <div class="modal-actions"><button class="btn" data-m="no">${esc(cancel)}</button>
        ${ok === null ? '' : `<button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-m="yes">${esc(ok)}</button>`}</div></div>`;
      root.classList.add('on');
      const close = (v) => { root.classList.remove('on'); root.innerHTML = ''; root.onclick = null; document.removeEventListener('keydown', onKey, true); resolve(v); };
      const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(false); } if (e.key === 'Enter' && ok !== null) { e.preventDefault(); close(true); } };
      // data-m="yes"/"no" resolve true/false; any other value (e.g. a choice button) resolves with that string
      root.onclick = (e) => { const m = e.target.closest('[data-m]'); if (m) close(m.dataset.m === 'yes' ? true : m.dataset.m === 'no' ? false : m.dataset.m); else if (e.target === root) close(false); };
      document.addEventListener('keydown', onKey, true);
      if (onShow) onShow(root);
      (root.querySelector('input') || root.querySelector('[data-m=yes]') || root.querySelector('[data-m]')).focus();
    });
  }

  // ---------------------------------------------------------------- rendering
  function render() {
    renderSidebar();
    renderMain();
    renderDrawer();
    renderSaveBar();
    updateJobUI();
    S.animate = false;
  }

  function renderSidebar() {
    const d = S.data;
    $('#sidebar').innerHTML = `
      <div class="brand"><div class="logo">M</div><div><b>ModHub</b><small>demo · in revisione</small></div></div>
      <div class="side-label">I tuoi giochi</div>
      <div class="games">${d.games.map((g) => `
        <button class="game-item ${g.id === S.gameId ? 'active' : ''}" data-act="game" data-id="${esc(g.id)}" style="--h:${GAME_HUE}">
          <div class="game-thumb">${esc(initials(g.name))}${g.found ? `<img src="/api/game-icon?id=${encodeURIComponent(g.id)}&t=${encodeURIComponent(T)}" alt="" onload="this.parentNode.classList.add('has-icon')" onerror="this.remove()">` : ''}</div>
          <span class="t">${esc(g.name.replace(/\s*\(.*\)$/, ''))}<em>${g.emulator ? esc(g.emulatorName) : g.found ? 'Installato' : 'Non trovato'}</em></span>
          <span class="dot ${g.found ? '' : 'off'}"></span>
        </button>`).join('') || '<div class="side-label" style="text-transform:none;letter-spacing:0">Nessun gioco nella libreria</div>'}
      </div>
      <button class="add-game" data-act="add-game">${ic('plus')} Aggiungi gioco</button>
      <div class="side-label" style="margin-top:12px">Sezioni</div>
      <nav class="nav">
        <button class="nav-item ${S.view === 'library' ? 'active' : ''}" data-act="view" data-id="library">${ic('library')} Libreria</button>
        <button class="nav-item ${S.view === 'settings' ? 'active' : ''}" data-act="view" data-id="settings" ${canSettings() ? '' : 'disabled'}
          title="${canSettings() ? '' : 'Disponibile quando il gioco è stato trovato'}">${ic('sliders')} Impostazioni${dirtyCount() ? '<i class="nav-dot"></i>' : ''}</button>
        <button class="nav-item ${S.view === 'emulators' ? 'active' : ''}" data-act="view" data-id="emulators">${ic('gamepad')} Emulatori</button>
        <button class="nav-item ${S.view === 'about' ? 'active' : ''}" data-act="view" data-id="about">${ic('info')} Informazioni</button>
      </nav>
      <div class="spacer"></div>
      <div class="source-box" style="margin-bottom:10px">
        <span class="k">Pannello in gioco</span>
        <span class="u">Tasti per aprirlo durante la partita</span>
        <select id="hotkeySel" aria-label="Tasti del pannello in gioco" style="margin-top:4px;width:100%">${hotkeyOptions()}</select>
      </div>
      <div class="source-box">
        <span class="k">Catalogo</span>
        <span class="v">${esc(d.catalogName || (d.loading ? 'Caricamento…' : 'Non disponibile'))}</span>
        <span class="u" title="${esc(d.source)}">${d.sourceIsDefault ? (d.offline ? 'Predefinito · copia offline (niente internet)' : 'Predefinito · online') : esc(d.source)}</span>
        <button class="btn btn-sm" data-act="source">${ic('link')} Cambia sorgente</button>
      </div>`;
  }

  function heroHtml(g) {
    if (!g) return `<section class="hero" style="--h:30"><div class="hero-tag">ModHub</div><h1>Nessun gioco disponibile</h1>
      <p class="lead" style="color:var(--muted)">Il catalogo non contiene giochi. Controlla la sorgente del catalogo.</p></section>`;
    const m = g.name.match(/^(.*?)\s*\((.*)\)$/);
    const title = m ? m[1] : g.name;
    const sub = m ? m[2] : '';
    const sel = S.profiles[g.id] || '|Standard';
    const sess = S.data.session && S.data.session.gid === g.id ? S.data.session : null;
    return `<section class="hero" style="--h:${GAME_HUE}">
      <div class="hero-tag">${ic('box')} ${g.emulator ? `Gioco per ${esc(g.emulatorName)}` : g.custom ? 'Gioco aggiunto da te' : 'Gioco'}</div>
      <h1>${esc(title)}${sub ? `<small>${esc(sub)}</small>` : ''}</h1>
      <div class="chips">
        ${g.base ? `<span class="chip">Versione base <b>${esc(g.base)}</b></span>` : ''}
        ${g.found ? `<span class="chip" title="${esc(g.emulator ? g.rom : g.root)}">${ic('folder')}<b>${esc(g.emulator ? g.rom : g.root)}</b></span>` : ''}
      </div>
      ${g.found ? `<div class="play-row">
        <button class="btn btn-primary btn-play" data-act="play" ${busy() ? 'disabled' : ''}>${ic('play')} GIOCA</button>
        <div class="select-wrap"><small>Profilo di avvio</small>
          <select id="profileSel">${g.profiles.map((p) => { const v = `${p.project || ''}|${p.name}`; return `<option value="${esc(v)}" ${v === sel ? 'selected' : ''}>${esc(p.name)}</option>`; }).join('')}</select></div>
        ${g.custom ? `<button class="btn" data-act="open-folder">${ic('folder')} Apri cartella</button>
          <button class="btn btn-danger" data-act="remove-game">${ic('trash')} Rimuovi dalla libreria</button>`
          : `<button class="btn" data-act="folder">${ic('folder')} Cambia cartella</button>`}
      </div>${sess ? sessionHtml(sess) : ''}` : g.emulator && g.missing === 'emulator' ? `<div class="notice">${ic('warn')}<div><b>${esc(g.emulatorName)} non è impostato</b>Indica dove si trova l'emulatore per poter avviare questo gioco.</div>
        <button class="btn btn-green" data-act="view" data-id="emulators">${ic('gamepad')} Vai agli Emulatori</button>
        <button class="btn btn-danger btn-sm" data-act="remove-game">${ic('trash')} Rimuovi</button></div>`
        : g.emulator ? `<div class="notice">${ic('warn')}<div><b>File del gioco non trovato</b>Il file è stato spostato o cancellato. Puoi rimuovere il gioco dalla libreria e aggiungerlo di nuovo.</div>
        <button class="btn btn-danger" data-act="remove-game">${ic('trash')} Rimuovi</button></div>`
        : g.custom ? `<div class="notice">${ic('warn')}<div><b>Programma non trovato</b>Il file del gioco è stato spostato o cancellato. Puoi rimuoverlo dalla libreria e aggiungerlo di nuovo.</div>
        <button class="btn btn-danger" data-act="remove-game">${ic('trash')} Rimuovi</button></div>`
        : `<div class="notice">${ic('warn')}<div><b>Gioco non trovato</b>Scegli la cartella che contiene il gioco per poter installare mod e avviarlo.</div>
        <button class="btn btn-primary" data-act="folder">${ic('folder')} Scegli cartella…</button></div>`}
    </section>`;
  }

  function renderMain() {
    if (S.view === 'settings') return renderSettings();
    if (S.view === 'emulators') return renderEmulators();
    if (S.view === 'about') return renderAbout();
    const d = S.data, g = game(), list = projects();
    const nInst = list.filter((p) => p.installed).length;
    const nUpd = list.filter((p) => p.status === 'update').length;
    const tab = (id, label, n, hot) => `<button class="tab ${S.tab === id ? 'active' : ''}" data-act="tab" data-id="${id}">${label}<span class="n ${hot && n ? 'hot' : ''}">${n}</span></button>`;
    if (g && g.custom) {
      $('#main').innerHTML = `<div class="main-inner">${heroHtml(g)}
        <div class="grid" style="margin-top:24px"><div class="empty">${ic('box')}${g.emulator
          ? `<b>Questo gioco si avvia con ${esc(g.emulatorName)}</b>Grafica, audio e comandi si cambiano dentro l'emulatore.`
          : `<b>Per questo gioco non ci sono ancora mod nel catalogo</b>Puoi comunque avviarlo da qui. Se qualcuno pubblica mod per questo gioco, le troverai in questa pagina.`}</div></div></div>`;
      return;
    }
    $('#main').innerHTML = `<div class="main-inner">
      ${heroHtml(g)}
      ${d.error ? `<div class="notice" style="margin-top:18px">${ic('warn')}<div><b>Catalogo non raggiungibile</b>${esc(d.error)}</div>
        <button class="btn" data-act="refresh">Riprova</button></div>` : ''}
      <div class="toolbar">
        <div class="tabs">${tab('all', 'Tutti', list.length)}${tab('installed', 'Installati', nInst)}${tab('updates', 'Aggiornamenti', nUpd, true)}</div>
        <label class="search">${ic('search')}<input id="q" type="search" placeholder="Cerca mod, autori…" value="${esc(S.q)}" autocomplete="off"></label>
        <button class="icon-btn ${S.refreshing || d.loading ? 'spin' : ''}" data-act="refresh" title="Aggiorna elenco">${ic('refresh')}</button>
      </div>
      <div class="grid ${S.animate ? 'enter' : ''}" id="grid"></div>
    </div>`;
    renderGrid();
  }

  function filtered() {
    const q = S.q.trim().toLowerCase();
    return projects().filter((p) =>
      (S.tab === 'all' || (S.tab === 'installed' && p.installed) || (S.tab === 'updates' && p.status === 'update')) &&
      (!q || `${p.name} ${p.author} ${p.summary}`.toLowerCase().includes(q)));
  }

  function coverHtml(p, pill = true) {
    const pillHtml = !pill ? '' : p.status === 'update' ? `<span class="pill amber">${ic('up')} Aggiornamento</span>`
      : p.installed ? `<span class="pill green">${ic('check')} Installato</span>` : '';
    return `<div class="cover" style="--h:${hue(p.id)}"><span class="glyph">${esc(initials(p.name))}</span>${pillHtml}</div>`;
  }

  function cardFoot(p) {
    const job = S.data.job;
    if (job && job.state === 'running' && job.pid === p.id) {
      return `<div class="prog" data-prog="${esc(p.id)}"><span class="lbl"></span><div class="bar"><i></i></div></div>`;
    }
    const off = busy() || !game().found ? 'disabled' : '';
    let btn;
    if (!p.installed) btn = `<button class="btn btn-primary btn-sm" data-act="install" data-id="${esc(p.id)}" ${off}>${ic('download')} Installa</button>`;
    else if (p.status === 'update') btn = `<button class="btn btn-update btn-sm" data-act="install" data-id="${esc(p.id)}" ${off}>${ic('up')} Aggiorna</button>`;
    else btn = `<button class="btn btn-done btn-sm" data-act="open" data-id="${esc(p.id)}">${ic('check')} Installato</button>`;
    const ver = p.installed && p.status === 'update' ? `<s>v${esc(p.installed)}</s><b>v${esc(p.latest)}</b>` : `<b>v${esc(p.installed || p.latest)}</b>`;
    return `<span class="ver">${ver}</span>${btn}`;
  }

  function renderGrid() {
    const grid = $('#grid');
    if (!grid) return;
    if (S.data.loading && !S.data.projects.length) { grid.innerHTML = '<div class="skeleton"></div>'.repeat(3); return; }
    const list = filtered();
    if (!list.length) {
      const msg = projects().length === 0 ? ['Ancora nessuna mod', 'Quando i creatori pubblicano per questo gioco, le troverai qui.']
        : S.q ? ['Nessun risultato', 'Prova con un’altra ricerca.']
        : S.tab === 'updates' ? ['Tutto aggiornato', 'Non ci sono aggiornamenti disponibili.'] : ['Niente da mostrare', 'Installa qualcosa dalla scheda “Tutti”.'];
      grid.innerHTML = `<div class="empty">${ic('box')}<b>${msg[0]}</b>${msg[1]}</div>`;
      return;
    }
    grid.innerHTML = list.map((p, i) => `
      <article class="card" tabindex="0" data-act="open" data-id="${esc(p.id)}" style="--i:${i}">
        ${coverHtml(p)}
        <div class="card-body"><h3>${esc(p.name)}</h3><div class="by">di ${esc(p.author)}</div><p>${esc(p.summary)}</p></div>
        <div class="card-foot">${cardFoot(p)}</div>
      </article>`).join('');
    updateJobUI();
  }

  // ---------------------------------------------------------------- drawer
  function actionFor(p, version) {
    if (!p.installed) return { label: `Installa ${version}`, cls: 'btn-primary', icon: 'download' };
    const c = cmpv(version, p.installed);
    if (c > 0) return { label: `Aggiorna a ${version}`, cls: 'btn-update', icon: 'up' };
    if (c < 0) return { label: `Torna alla ${version}`, cls: 'btn-primary', icon: 'download' };
    return { label: 'Verifica / ripara', cls: '', icon: 'check' };
  }

  function renderDrawer() {
    const el = $('#drawer'), p = project(S.openId);
    $('#scrim').classList.toggle('on', !!p);
    el.classList.toggle('open', !!p);
    el.setAttribute('aria-hidden', String(!p));
    if (!p) return;
    const m = S.detail && S.detail.manifest.id === p.id ? S.detail.manifest : null;
    const versions = m ? Object.keys(m.versions).sort((a, b) => cmpv(b, a)) : [];
    const version = S.version || p.latest;
    const act = actionFor(p, version);
    const off = busy() || !m || !game().found;
    const job = S.data.job;
    const running = job && job.state === 'running' && job.pid === p.id;
    const files = m && m.versions[version] ? m.versions[version].files : [];
    el.innerHTML = `
      ${coverHtml(p, false)}
      <button class="icon-btn close" data-act="close" aria-label="Chiudi">${ic('x')}</button>
      <div class="drawer-body">
        <h2>${esc(p.name)}</h2>
        <div class="by">di ${esc(p.author)}</div>
        <div class="inline-pills">
          ${p.status === 'update' ? `<span class="pill amber">${ic('up')} Aggiornamento disponibile</span>` : p.installed ? `<span class="pill green">${ic('check')} Installato v${esc(p.installed)}</span>` : ''}
        </div>
        <p class="lead">${esc(p.summary)}</p>
        <div class="panel">
          <div class="panel-row">
            <div class="select-wrap"><small>Versione</small>
              <select id="verSel" ${m ? '' : 'disabled'}>${versions.map((v) => `<option value="${esc(v)}" ${v === version ? 'selected' : ''}>${esc(v)}${v === p.latest ? ' · ultima' : ''}</option>`).join('')}</select></div>
            <button class="btn ${act.cls}" data-act="install-sel" ${off ? 'disabled' : ''}>${ic(act.icon)} ${esc(act.label)}</button>
          </div>
          ${running ? `<div class="prog" data-prog="${esc(p.id)}"><span class="lbl"></span><div class="bar"><i></i></div></div>` : ''}
        </div>
        ${m ? `
          ${m.description ? `<h4>Descrizione</h4><p class="desc">${esc(m.description)}</p>` : ''}
          <h4>Novità</h4>
          <ul class="timeline">${versions.map((v) => `<li class="${v === p.installed ? 'cur' : ''}"><b>${esc(v)}</b><time>${esc(m.versions[v].date || '')}</time>${v === p.installed ? '<time>· installata</time>' : ''}<div>${esc(m.versions[v].changelog || '')}</div></li>`).join('')}</ul>
          <h4>File della versione ${esc(version)}</h4>
          <details><summary>${files.length ? `${files.length} file` : 'Nessun file: aggiunge solo un profilo di avvio'}</summary>
            ${files.length ? `<ul class="files">${files.map((f) => { const x = EXEC.has((f.path.match(/\.[^./\\]+$/) || [''])[0].toLowerCase()); return `<li class="${x ? 'exec' : ''}"><span>${esc(f.path)}${x ? ' ⚠' : ''}</span><span class="sz">${bytes(f.size)}</span></li>`; }).join('')}</ul>` : ''}
          </details>` : '<h4>Dettagli</h4><div class="skeleton" style="height:90px"></div>'}
      </div>
      <div class="drawer-foot">
        <small>${p.installed ? `Installata: v${esc(p.installed)}` : 'Non installato'}</small>
        ${p.installed ? `<button class="btn btn-danger btn-sm" data-act="uninstall" data-id="${esc(p.id)}" ${busy() ? 'disabled' : ''}>${ic('trash')} Disinstalla</button>` : ''}
      </div>`;
  }

  async function openDrawer(id) {
    S.openId = id; S.detail = null; S.version = null;
    renderDrawer();
    try {
      const detail = await api(`project?id=${encodeURIComponent(id)}`);
      if (S.openId !== id) return;
      S.detail = detail;
      S.version = project(id).latest;
      renderDrawer();
    } catch (e) { toast(e.message, 'error'); }
  }
  function closeDrawer() { S.openId = null; renderDrawer(); }

  // ---------------------------------------------------------------- progress
  function updateJobUI() {
    const job = S.data && S.data.job;
    const on = !!job && job.state === 'running';
    const top = $('#topProgress');
    top.classList.toggle('on', on);
    top.firstElementChild.style.width = on ? `${Math.max(job.pct, 4)}%` : '0';
    document.querySelectorAll('[data-prog]').forEach((el) => {
      const mine = on && job.pid === el.dataset.prog;
      const bar = el.querySelector('.bar');
      bar.classList.toggle('busy', mine && (job.kind === 'uninstall' || job.pct === 0));
      el.querySelector('i').style.width = mine ? `${job.pct}%` : '0';
      el.querySelector('.lbl').textContent = !mine ? '' : job.kind === 'uninstall' ? 'Disinstallazione…'
        : job.kind === 'emu-install' ? (job.stage === 'Download' && job.pct ? `Download ${job.pct}%` : `${job.stage || 'Preparazione'}…`)
        : job.pct ? `Download ${job.pct}%` : 'Preparazione…';
    });
  }

  async function poll() {
    if (S.polling) return;
    S.polling = true;
    try {
      for (;;) {
        S.data = await api('state');
        const job = S.data.job;
        if (!job || job.state !== 'running') break;
        updateJobUI();
        await sleep(220);
      }
      const job = S.data.job;
      if (job && job.id !== S.lastJob) { S.lastJob = job.id; toast(job.message, job.state === 'error' ? 'error' : 'ok'); }
      render();
      if (S.openId) { const id = S.openId; S.detail = null; api(`project?id=${encodeURIComponent(id)}`).then((d) => { if (S.openId === id) { S.detail = d; renderDrawer(); } }).catch(() => {}); }
    } catch (e) { toast('Connessione al programma persa.', 'error'); }
    finally { S.polling = false; }
  }

  // ---------------------------------------------------------------- actions
  async function load() {
    try { S.data = await api('state'); } catch (e) { toast('Connessione al programma persa.', 'error'); return; }
    const d = S.data;
    if (!d.games.some((g) => g.id === S.gameId)) S.gameId = (d.games.find((g) => g.found) || d.games[0] || {}).id || null;
    if (S.view === 'settings' && !S.setTarget && !canSettings()) { S.view = 'library'; S.set = null; }
    if (S.lastJob === null) S.lastJob = d.job ? d.job.id : 0;
    noticeCheck(d);
    S.refreshing = false;
    render();
    if (d.loading) setTimeout(load, 350);
    if (d.job && d.job.state === 'running') poll();
    if (S.view === 'emulators') scanEmulators();
    if (!S.booted && !d.loading) {
      S.booted = true;
      const wanted = location.hash.slice(1);  // open a section straight from the address, e.g. ...#emulators
      if (wanted === 'emulators' || wanted === 'settings' || wanted === 'about') changeView(wanted);
      else if (wanted.startsWith('project-')) openDrawer(wanted.slice('project-'.length));
      else if (wanted.startsWith('emu-settings-')) openEmuSettings(wanted.slice('emu-settings-'.length));
    }
  }

  // settings that were waiting for a game to close get applied by the server: tell the user once
  function noticeCheck(d) {
    const n = d.notice;
    if (S.lastNotice === undefined) { S.lastNotice = n ? n.id : 0; return; }
    if (n && n.id !== S.lastNotice) { S.lastNotice = n.id; toast(n.text, n.error ? 'error' : 'ok'); }
  }

  // follow the running game: show/hide the "in gioco" strip and refresh the settings page when it starts or closes
  async function watchSession() {
    if (document.hidden || S.polling || !S.data) return;
    let d;
    try { d = await api('state'); } catch (e) { return; }
    noticeCheck(d);
    if (JSON.stringify(d.session) === JSON.stringify(S.data.session)) { S.data.notice = d.notice; return; }
    S.data = d;
    render();
    if (S.view === 'settings' && S.set) loadSettings(true);
  }

  async function install(id, version) {
    const p = project(id);
    version = version || p.latest;
    let detail;
    try { detail = await api(`project?id=${encodeURIComponent(id)}`); } catch (e) { return toast(e.message, 'error'); }
    const risky = detail.risky[version] || [];
    if (risky.length) {
      const ok = await modal({
        icon: 'shield', title: 'Questa mod contiene codice eseguibile',
        text: `«${esc(p.name)}» ${esc(version)} installa file che vengono <b>eseguiti sul tuo PC</b>. Procedi solo se ti fidi di <b>${esc(p.author)}</b>.`,
        extra: `<ul>${risky.slice(0, 8).map((f) => `<li>${esc(f)}</li>`).join('')}</ul>`, ok: 'Installa comunque',
      });
      if (!ok) return;
    }
    try { await api('install', { id, version, confirmed: true }); } catch (e) { return toast(e.message, 'error'); }
    S.data = await api('state');
    render();
    poll();
  }

  async function uninstall(id) {
    const p = project(id);
    const ok = await modal({ icon: 'trash', title: `Disinstallare ${p.name}?`, danger: true, ok: 'Disinstalla',
      text: 'I file della mod vengono rimossi e gli eventuali file originali sostituiti tornano al loro posto.' });
    if (!ok) return;
    try { await api('uninstall', { id }); } catch (e) { return toast(e.message, 'error'); }
    S.data = await api('state');
    render();
    poll();
  }

  async function play() {
    const g = game();
    const [project_, ...rest] = (S.profiles[g.id] || '|Standard').split('|');
    const name = rest.join('|');
    try {
      const r = await api('play', { game: g.id, project: project_ || null, name });
      const s = r.session;
      toast(!s ? `Avvio in corso con il profilo «${name}»…`
        : s.hotkey ? `Avviato. In partita premi ${s.hotkey} per il pannello di ModHub${s.own ? ` (${s.own} apre il menu del gioco)` : ''}.`
        : s.hotkeyClash ? `Avviato. ${hotkeyLabel()} è usato dal gioco stesso: cambia i tasti del pannello a sinistra.`
        : s.hotkeyBusy ? `Avviato. ${hotkeyLabel()} è già usato da un altro programma: apri il pannello da qui.` : `Avvio in corso con il profilo «${name}»…`);
      S.data = await api('state');
      render();
    } catch (e) { toast(e.message, 'error'); }
  }

  async function chooseFolder() {
    try { const r = await api('pick-folder', { game: S.gameId }); if (r.picked) { toast('Cartella del gioco impostata.'); await load(); } }
    catch (e) { toast(e.message, 'error'); }
  }

  async function addGameMenu() {
    if (!(await leaveSettingsOk())) return;
    const choice = await modal({
      icon: 'plus', title: 'Cosa vuoi aggiungere?', ok: null,
      extra: `<div class="choices">
        <button class="choice" data-m="pc"><span class="ci">${ic('monitor')}</span><span><b>Un programma del PC</b><small>Un gioco per Windows: scegli il suo file .exe</small></span></button>
        <button class="choice" data-m="emu"><span class="ci">${ic('gamepad')}</span><span><b>Un gioco per emulatore</b><small>ROM o ISO di PS2, Wii, PS3, Nintendo DS e altre console</small></span></button>
      </div>`,
    });
    if (choice === 'pc') await addGame();
    else if (choice === 'emu') await addEmuGame(null);
  }

  async function addEmuGame(eid) {
    const ready = S.data.emulators.filter((e) => e.configured);
    if (!eid) {
      if (!ready.length) { toast('Prima imposta un emulatore: ti porto nella sezione Emulatori.', 'error'); await changeView('emulators'); return; }
      let sel;
      const ok = await modal({ icon: 'gamepad', title: 'Con quale emulatore?', ok: 'Scegli il gioco…',
        text: 'Poi ti chiedo di selezionare il file del gioco.',
        extra: `<select id="emuSel" style="width:100%">${ready.map((e) => `<option value="${esc(e.id)}">${esc(e.name)} · ${esc(e.systems.join(', ') || 'personalizzato')}</option>`).join('')}</select>`,
        onShow: (root) => { sel = root.querySelector('#emuSel'); } });
      if (!ok) return;
      eid = sel.value;
    }
    let r;
    try { r = await api('pick-rom', { emulator: eid }); } catch (e) { return toast(e.message, 'error'); }
    if (!r.picked) return;
    let input;
    const ok = await modal({
      icon: 'gamepad', title: 'Aggiungi alla libreria', ok: 'Aggiungi', text: 'Come vuoi chiamare questo gioco?',
      extra: `<input type="text" id="gameName" spellcheck="false" maxlength="80" value="${esc(r.name)}"><p class="hint" style="word-break:break-all">${esc(r.exe)}</p>`,
      onShow: (root) => { input = root.querySelector('#gameName'); input.select(); },
    });
    if (!ok) return;
    try {
      const res = await api('add-game', { name: input ? input.value : r.name });
      S.gameId = res.id; S.view = 'library'; S.set = null; S.animate = true;
      toast('Gioco aggiunto alla libreria.');
      await load();
    } catch (e) { toast(e.message, 'error'); }
  }

  async function emuAction(act, id) {
    const emu = S.data.emulators.find((e) => e.id === id);
    try {
      if (act === 'emu-detect') {
        const r = await api('emu-detect', { emulator: id });
        if (r.found) toast(`${emu.name} trovato: ${r.path}`);
        else toast(`Non trovo ${emu.name} nelle cartelle solite. Scaricalo dal sito ufficiale oppure indica dove si trova.`, 'error');
      } else if (act === 'emu-pick') {
        const r = await api('emu-pick', { emulator: id });
        if (r.picked) toast(`${emu.name} impostato.`);
      } else if (act === 'emu-site') {
        await api('emu-site', { emulator: id });
        toast('Ho aperto il sito ufficiale nel browser.');
      } else if (act === 'emu-forget') {
        const ok = await modal({ icon: 'trash', title: `Togliere ${emu.name}?`, danger: true, ok: 'Togli',
          text: emu.custom ? 'L’emulatore sparisce dall’elenco. Il programma sul PC non viene toccato.'
            : 'ModHub dimentica dove si trova. Il programma sul PC non viene toccato e i giochi restano nella libreria.' });
        if (!ok) return;
        await api('emu-forget', { emulator: id });
      }
    } catch (e) { toast(e.message, 'error'); }
    await load();
  }

  async function addCustomEmulator() {
    let r;
    try { r = await api('emu-custom-pick', {}); } catch (e) { return toast(e.message, 'error'); }
    if (!r.picked) return;
    let name, args;
    const ok = await modal({
      icon: 'gamepad', title: 'Emulatore personalizzato', ok: 'Aggiungi',
      text: 'Indica come si chiama e come si avvia un gioco. Scrivi <b>{rom}</b> dove va il percorso del gioco.',
      extra: `<input type="text" id="ceName" spellcheck="false" maxlength="60" value="${esc(r.name)}" placeholder="Nome">
        <input type="text" id="ceArgs" spellcheck="false" value="{rom}" placeholder="Argomenti, es. -f {rom}" style="margin-top:8px">
        <p class="hint">Quasi tutti gli emulatori vogliono solo il percorso del gioco: lascia <b>{rom}</b>. Se serve altro, guarda la guida dell’emulatore (esempio: <b>-fullscreen {rom}</b>).</p>
        <p class="hint" style="word-break:break-all">${esc(r.exe)}</p>`,
      onShow: (root) => { name = root.querySelector('#ceName'); args = root.querySelector('#ceArgs'); name.select(); },
    });
    if (!ok) return;
    try { await api('emu-custom-add', { name: name.value, args: args.value }); toast('Emulatore aggiunto.'); await load(); }
    catch (e) { toast(e.message, 'error'); }
  }

  function emuFoot(e) {
    const id = esc(e.id), off = busy() ? 'disabled' : '';
    const job = S.data.job;
    if (job && job.state === 'running' && job.pid === e.id) {
      return `<div class="prog" data-prog="${id}"><span class="lbl"></span><div class="bar"><i></i></div></div>`;
    }
    const upd = S.updates[e.id];
    if (e.configured && e.managed) {
      return `<button class="btn btn-green btn-sm" data-act="emu-add-game" data-id="${id}" ${off}>${ic('plus')} Aggiungi gioco</button>
        ${e.hasSettings ? `<button class="btn btn-sm" data-act="emu-settings" data-id="${id}">${ic('sliders')} Impostazioni</button>` : ''}
        ${upd && upd.available ? `<button class="btn btn-update btn-sm" data-act="emu-download" data-id="${id}" ${off}>${ic('up')} Aggiorna a ${esc(upd.version)}</button>` : ''}
        <span class="grow"></span>
        <button class="btn btn-sm" data-act="emu-open" data-id="${id}" title="Apri la cartella dell’emulatore">${ic('folder')}</button>
        <button class="btn btn-sm btn-danger" data-act="emu-uninstall" data-id="${id}" title="Disinstalla" ${off}>${ic('trash')}</button>`;
    }
    if (e.configured) {
      return `<button class="btn btn-green btn-sm" data-act="emu-add-game" data-id="${id}">${ic('plus')} Aggiungi gioco</button>
        ${e.hasSettings ? `<button class="btn btn-sm" data-act="emu-settings" data-id="${id}">${ic('sliders')} Impostazioni</button>` : ''}
        <span class="grow"></span>
        <button class="btn btn-sm" data-act="emu-pick" data-id="${id}" title="Cambia programma">${ic('folder')}</button>
        <button class="btn btn-sm btn-danger" data-act="emu-forget" data-id="${id}" title="Togli">${ic('trash')}</button>`;
    }
    if (S.found[e.id]) {  // already installed somewhere on this PC: offer to use it before downloading anything
      return `<button class="btn btn-green btn-sm" data-act="emu-detect" data-id="${id}">${ic('check')} Usa quello che hai già</button>
        ${e.canInstall ? `<button class="btn btn-sm" data-act="emu-download" data-id="${id}" ${off}>${ic('download')} Scarica comunque</button>` : ''}
        <button class="btn btn-sm" data-act="emu-pick" data-id="${id}">${ic('folder')} Percorso…</button>`;
    }
    return `${e.canInstall ? `<button class="btn btn-green btn-sm" data-act="emu-download" data-id="${id}" ${off}>${ic('download')} Scarica e installa</button>` : ''}
      <button class="btn btn-sm" data-act="emu-detect" data-id="${id}">${ic('search')} Rileva sul PC</button>
      <button class="btn btn-sm" data-act="emu-pick" data-id="${id}">${ic('folder')} Percorso…</button>
      ${e.site ? `<button class="btn btn-sm" data-act="emu-site" data-id="${id}">${ic('link')} Sito ufficiale</button>` : ''}`;
  }

  async function emuDownload(id) {
    const emu = S.data.emulators.find((e) => e.id === id);
    let plan;
    try { toast('Controllo l’ultima versione sul sito ufficiale…'); plan = await api('emu-plan', { emulator: id }); }
    catch (e) { return toast(e.message, 'error'); }
    const ok = await modal({
      icon: 'download', title: `${plan.update ? 'Aggiorna' : 'Scarica e installa'} ${emu.name}`, ok: plan.update ? 'Aggiorna' : 'Scarica e installa',
      text: 'ModHub scarica l’emulatore dal sito ufficiale del progetto e lo installa in una cartella sua, senza toccare il resto del PC.',
      extra: `<ul class="facts">
        <li><span>File</span><b>${esc(plan.name)}</b></li>
        <li><span>Versione</span><b>${esc(plan.version)}</b></li>
        <li><span>Dimensione</span><b>${plan.size ? bytes(plan.size) : 'non indicata'}</b></li>
        <li><span>Da</span><b>${esc(plan.host)}</b></li>
        <li><span>Controllo</span><b class="${plan.verified ? 'good' : 'warn'}">${plan.verified ? 'impronta SHA-256 verificata' : 'il progetto non pubblica un’impronta: fidati solo del sito'}</b></li>
      </ul>${plan.update ? '<p class="hint">Salvataggi e impostazioni dentro la cartella dell’emulatore restano dove sono.</p>' : ''}`,
    });
    if (!ok) return;
    try { await api('emu-install', { emulator: id, asset: plan.name }); } catch (e) { return toast(e.message, 'error'); }
    delete S.updates[id];
    S.data = await api('state');
    render();
    poll();
  }

  async function emuUninstall(id) {
    const emu = S.data.emulators.find((e) => e.id === id);
    const ok = await modal({ icon: 'trash', title: `Disinstallare ${emu.name}?`, danger: true, ok: 'Disinstalla',
      text: `Viene cancellata la cartella dell’emulatore, <b>compresi i suoi salvataggi e le sue impostazioni</b> se li tiene lì. I giochi restano nella libreria e tornano a funzionare quando lo reinstalli.` });
    if (!ok) return;
    try { const r = await api('emu-uninstall', { emulator: id }); toast(`${r.name} disinstallato.`); }
    catch (e) { toast(e.message, 'error'); }
    delete S.updates[id];
    await load();
  }

  async function scanEmulators() {
    try { S.found = (await api('emu-scan', {})).found; } catch (e) { S.found = {}; }
    if (S.view === 'emulators' && $('#emuGrid')) $('#emuGrid').innerHTML = emuCards();
  }

  async function checkEmuUpdates() {
    try {
      toast('Cerco aggiornamenti…');
      const r = await api('emu-check', {});
      S.updates = Object.fromEntries(Object.entries(r.updates).filter(([, v]) => !v.error));
      const n = Object.values(S.updates).filter((u) => u.available).length;
      const errors = Object.values(r.updates).filter((u) => u.error).length;
      toast(n ? `${n} ${n === 1 ? 'aggiornamento disponibile' : 'aggiornamenti disponibili'}.` : errors ? 'Non sono riuscito a controllare tutti gli emulatori.' : 'Tutti gli emulatori sono aggiornati.');
    } catch (e) { toast(e.message, 'error'); }
    renderMain();
  }

  function emuCards() {
    const q = S.qe.trim().toLowerCase();
    const list = S.data.emulators.filter((e) => !q || `${e.name} ${e.systems.join(' ')} ${(e.aliases || []).join(' ')}`.toLowerCase().includes(q));
    const cards = list.map((e, i) => `
      <article class="card static" style="--i:${i}">
        <div class="cover" style="--h:${hue(e.id)}"><span class="glyph">${esc((e.aliases && e.aliases[0] ? e.aliases[0] : initials(e.name)).toUpperCase())}</span>
          ${S.updates[e.id] && S.updates[e.id].available ? `<span class="pill amber">${ic('up')} Aggiornamento</span>`
            : e.configured ? `<span class="pill green">${ic('check')} ${e.version ? `v${esc(e.version)}` : 'Pronto'}</span>`
            : S.found[e.id] ? `<span class="pill green">${ic('check')} Trovato sul PC</span>` : ''}</div>
        <div class="card-body"><h3>${esc(e.name)}</h3>
          <div class="chips tight">${(e.systems.length ? e.systems : ['Personalizzato']).map((s) => `<span class="chip sm">${esc(s)}</span>`).join('')}</div>
          ${!e.configured && S.found[e.id] ? `<p class="mono found" title="${esc(S.found[e.id])}">Già sul tuo PC: ${esc(S.found[e.id])}</p>` : ''}
          ${e.configured ? `<p class="mono" title="${esc(e.path)}">${esc(e.path)}</p><p class="count"><b>${e.games}</b> ${e.games === 1 ? 'gioco' : 'giochi'} nella libreria</p>` : ''}
          ${e.needsBios ? '<p class="small">Richiede il BIOS della console, che non è incluso.</p>' : ''}
        </div>
        <div class="card-foot wrap">${emuFoot(e)}</div>
      </article>`).join('');
    const add = `<button class="card static add-card" data-act="emu-custom">${ic('plus')}<b>Emulatore personalizzato</b>
      <span>Ne usi uno che non è in elenco? Indica il suo programma e come avviare un gioco.</span></button>`;
    return cards + (q && !list.length ? '<div class="empty">Nessun emulatore trovato: aggiungilo come personalizzato.</div>' : '') + add;
  }

  function renderAbout() {
    const feedback = S.data.feedback;
    const item = (icon, title, text) => `<div class="about-item">${ic(icon)}<div><b>${title}</b><span>${text}</span></div></div>`;
    $('#main').innerHTML = `<div class="main-inner">
      <header class="set-head">
        <div><div class="hero-tag">${ic('info')} Informazioni</div><h1>Cos’è ModHub</h1>
          <p class="lead muted">Un’unica app, gratuita, per i giochi “fatti dalla comunità”: port per PC, mod, patch ed emulatori.
          Come una piccola Steam, ma per le mod: le trovi, le installi con un clic e restano aggiornate.</p></div>
      </header>
      <div class="notice about-demo">${ic('warn')}<div><b>Questa è una versione demo</b>ModHub è un prototipo: alcune parti vanno ancora riviste e
        possono esserci errori. Fai una copia dei tuoi salvataggi prima di provare cose nuove.</div></div>
      <h3 class="about-h">A cosa serve</h3>
      <div class="about-grid">
        ${item('library', 'Libreria dei tuoi giochi', 'Port per PC, giochi scaricati e giochi per emulatore in un posto solo, con un pulsante GIOCA.')}
        ${item('download', 'Mod con un clic', 'Installa, aggiorna o togli mod e patch dal catalogo. ModHub controlla ogni file scaricato e tiene una copia di quelli che sostituisce.')}
        ${item('up', 'Sempre aggiornate', 'Quando un autore pubblica una nuova versione compare “Aggiorna”, con le novità scritte in chiaro.')}
        ${item('sliders', 'Impostazioni semplici', 'Grafica, audio e comandi del gioco con interruttori e cursori, senza aprire file di configurazione.')}
        ${item('bolt', 'Ottimizza per il tuo PC', 'Riconosce la scheda video e propone il livello giusto, da “Prestazioni massime” a “Qualità massima”.')}
        ${item('gamepad', 'Emulatori inclusi', 'PCSX2, Dolphin, RPCS3 e altri: ModHub li scarica, li imposta e avvia i tuoi giochi con quello giusto.')}
        ${item('monitor', 'Pannello in gioco', `Durante la partita premi ${esc(hotkeyLabel())}: un piccolo pannello per cambiare impostazioni mentre giochi, molte subito.`)}
        ${item('shield', 'Niente giochi pirata nel catalogo', 'Il catalogo contiene solo mod, patch e impostazioni: il gioco lo devi avere tu.')}
      </div>
      <h3 class="about-h">Consigli benvenuti</h3>
      <div class="panel about-feedback"><p>I consigli sono accettati <b>vivamente</b>: cosa non è chiaro, cosa non funziona, cosa manca,
        quale gioco o mod vorresti vedere. Ogni segnalazione aiuta a migliorare la prossima versione.</p>
        ${feedback ? `<a class="btn btn-green" href="${esc(feedback)}" target="_blank" rel="noopener">${ic('link')} Lascia un consiglio</a>` : ''}</div>
    </div>`;
  }

  function renderEmulators() {
    const n = S.data.emulators.filter((e) => e.configured).length;
    $('#main').innerHTML = `<div class="main-inner">
      <header class="set-head">
        <div><div class="hero-tag">${ic('gamepad')} Emulatori</div><h1>Giochi di altre console</h1>
          <p class="lead muted">Imposta un emulatore, poi aggiungi i tuoi giochi (ROM o ISO): ModHub li avvia per te con l’emulatore giusto.</p></div>
        <div class="head-actions">
          ${S.data.emulators.some((e) => e.managed) ? `<button class="btn" data-act="emu-check" ${busy() ? 'disabled' : ''}>${ic('refresh')} Cerca aggiornamenti</button>` : ''}
          <button class="btn btn-green" data-act="emu-add-game" ${n ? '' : 'disabled title="Prima imposta un emulatore"'}>${ic('plus')} Aggiungi gioco</button>
        </div>
      </header>
      <div class="toolbar" style="margin-top:6px">
        <span class="muted"><b>${n}</b> di ${S.data.emulators.length} emulatori pronti</span>
        <label class="search">${ic('search')}<input id="qe" type="search" placeholder="Cerca per nome o console (es. PS3, Wii)…" value="${esc(S.qe)}" autocomplete="off"></label>
      </div>
      <div class="grid ${S.animate ? 'enter' : ''}" id="emuGrid">${emuCards()}</div>
    </div>`;
  }

  async function addGame() {
    let r;
    try { r = await api('pick-game', {}); } catch (e) { return toast(e.message, 'error'); }
    if (!r.picked) return;
    if (r.linked) { S.gameId = r.linked; S.view = 'library'; S.set = null; toast(`«${r.name}» è già nel catalogo: ho collegato la sua cartella.`); await load(); return; }
    let input;
    const ok = await modal({
      icon: 'gamepad', title: 'Aggiungi alla libreria', ok: 'Aggiungi',
      text: 'Come vuoi chiamare questo gioco?',
      extra: `<input type="text" id="gameName" spellcheck="false" maxlength="80" value="${esc(r.name)}"><p class="hint" style="word-break:break-all">${esc(r.exe)}</p>`,
      onShow: (root) => { input = root.querySelector('#gameName'); input.select(); },
    });
    if (!ok) return;
    try {
      const res = await api('add-game', { name: input ? input.value : r.name });
      S.gameId = res.id; S.view = 'library'; S.set = null; S.animate = true;
      toast('Gioco aggiunto alla libreria.');
      await load();
    } catch (e) { toast(e.message, 'error'); }
  }

  async function removeGame() {
    const g = game();
    const ok = await modal({ icon: 'trash', title: `Rimuovere ${g.name}?`, danger: true, ok: 'Rimuovi',
      text: 'Il gioco sparisce solo dalla libreria di ModHub: i suoi file sul PC non vengono toccati.' });
    if (!ok) return;
    try { await api('remove-game', { game: g.id }); S.gameId = null; toast('Gioco rimosso dalla libreria.'); await load(); }
    catch (e) { toast(e.message, 'error'); }
  }

  async function changeSource() {
    let input;
    const ok = await modal({
      icon: 'link', title: 'Sorgente del catalogo', ok: 'Salva',
      text: 'Indirizzo web (https://…) o cartella locale di un catalogo ModHub.',
      extra: `<input type="text" id="srcInput" spellcheck="false" value="${S.data.sourceIsDefault ? '' : esc(S.data.source)}" placeholder="Vuoto = catalogo predefinito di ModHub"><p class="hint">Lascia vuoto per usare il catalogo predefinito di ModHub. Chiunque può ospitare un catalogo: basta una cartella pubblicata su un sito.</p>`,
      onShow: (root) => { input = root.querySelector('#srcInput'); input.select(); },
    });
    if (!ok) return;
    try { await api('source', { value: input ? input.value : '' }); S.refreshing = true; await sleep(150); await load(); }
    catch (e) { toast(e.message, 'error'); }
  }

  async function refresh() {
    S.refreshing = true;
    renderMain();
    try { await api('refresh', {}); } catch (e) { toast(e.message, 'error'); }
    await sleep(250);
    await load();
  }

  // ---------------------------------------------------------------- game settings
  const canSettings = () => { const g = game(); return !!(g && g.found && g.hasSettings); };
  const dirtyCount = () => (S.set ? Object.keys(S.set.draft).length : 0);
  const fieldsOf = (group) => group.sections.flatMap((s) => s.fields.filter((f) => f.type !== 'info'));
  const allFields = () => S.set.schema.groups.flatMap(fieldsOf);
  const same = (a, b) => (typeof a === 'number' && typeof b === 'number' ? Math.abs(a - b) < 1e-9 : a === b);
  // what the user sees as "current": their unsaved draft, else settings queued for when the game closes, else the file, else the default
  const baseVal = (f) => (f.key in S.set.pending ? S.set.pending[f.key] : f.key in S.set.saved ? S.set.saved[f.key] : f.default);
  const curVal = (f) => (f.key in S.set.draft ? S.set.draft[f.key] : baseVal(f));
  const fmtSlider = (f, v) => (f.percent ? `${Math.round(v * 100)}%` : `${v}${f.unit ? ` ${f.unit}` : ''}`);
  const fillPct = (lo, hi, v) => (hi > lo ? ((v - lo) / (hi - lo)) * 100 : 0);
  // a settings page edits a game, or an emulator ("emu:<id>") when opened from the Emulators page
  const settingsTarget = () => S.setTarget || S.gameId;

  async function loadSettings(keepDraft = false) {
    const tid = settingsTarget();
    try {
      const d = await api(`settings?game=${encodeURIComponent(tid)}`);
      if (settingsTarget() !== tid || S.view !== 'settings') return;
      const prev = S.set && S.set.target === d.target ? S.set : null;
      S.set = { target: d.target, title: d.title, schema: d.schema, saved: d.values, pending: d.pending || {}, exists: d.exists,
                running: d.running, hasBackup: d.hasBackup, path: d.path, draft: keepDraft && prev ? prev.draft : {},
                tab: (prev && prev.tab) || d.schema.groups[0].id };
    } catch (e) {
      toast(e.message, 'error'); S.view = 'library'; S.setTarget = null; S.set = null;
    }
    if (!keepDraft) S.optLevel = null;
    render();
    if (S.set && S.set.schema.optimizer && !S.hw) loadHardware();
  }

  // "Ctrl+Shift+Tab" -> <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>M</kbd>
  const kbds = (label) => label.split('+').map((k) => `<kbd>${esc(k)}</kbd>`).join(' + ');
  const hotkeyLabel = () => (S.data && S.data.prefs ? S.data.prefs.overlayHotkey : 'Ctrl+Shift+Tab');

  function hotkeyOptions() {
    const p = S.data.prefs || { overlayHotkey: 'Ctrl+Shift+Tab', hotkeyPresets: ['Ctrl+Shift+Tab'] };
    const list = p.hotkeyPresets.includes(p.overlayHotkey) ? p.hotkeyPresets : [p.overlayHotkey, ...p.hotkeyPresets];
    return list.map((h) => `<option value="${esc(h)}" ${h === p.overlayHotkey ? 'selected' : ''}>${esc(h)}</option>`).join('');
  }

  async function changeHotkey(value) {
    try {
      const prefs = await api('prefs', { overlayHotkey: value });
      S.data.prefs = prefs;
      toast(`Il pannello in gioco ora si apre con ${prefs.overlayHotkey}.`);
      S.data = await api('state');
    } catch (e) { toast(e.message, 'error'); }
    render();
  }

  function sessionHtml(sess) {
    const own = sess.own ? ` Il menu del gioco si apre con <kbd>${esc(sess.own)}</kbd>.` : '';
    const hint = sess.hotkeyClash ? `Questa combinazione è già usata dal gioco (${esc(sess.own)}): scegline un'altra qui a sinistra.`
      : sess.hotkey ? `In partita premi ${kbds(sess.hotkey)} per aprire il pannello di ModHub.${own}`
      : sess.hotkeyBusy ? `${esc(hotkeyLabel())} è già usato da un altro programma: scegli altri tasti qui a sinistra.` : own;
    return `<div class="session"><span class="live"><i></i> In gioco</span><span>${hint}</span>
      ${sess.target ? `<button class="btn btn-sm" data-act="overlay-open">${ic('sliders')} Apri il pannello</button>` : ''}</div>`;
  }

  async function openEmuSettings(id) {
    if (!(await leaveSettingsOk())) return;
    S.setTarget = `emu:${id}`; S.view = 'settings'; S.set = null; S.openId = null; S.animate = true;
    render();
    $('#main').scrollTop = 0;
    await loadSettings();
  }

  const confirmDiscard = () => modal({ icon: 'warn', title: 'Modifiche non salvate', danger: true, ok: 'Esci senza salvare',
    text: 'Se esci ora perdi le modifiche che hai fatto alle impostazioni.' });

  async function leaveSettingsOk() {
    if (S.view !== 'settings' || !dirtyCount()) return true;
    return confirmDiscard();
  }

  async function changeView(view) {
    if (view === S.view && !(view === 'settings' && S.setTarget)) return;
    if (view === 'settings' && !canSettings()) return;
    if (!(await leaveSettingsOk())) return;
    S.view = view; S.set = null; S.openId = null; S.animate = true; S.setTarget = null;
    render();
    $('#main').scrollTop = 0;
    if (view === 'settings') await loadSettings();
    if (view === 'emulators') scanEmulators();
  }

  async function switchGame(id) {
    if (id === S.gameId && S.view === 'library') return;
    if (!(await leaveSettingsOk())) return;
    S.gameId = id; S.view = 'library'; S.set = null; S.setTarget = null; S.animate = true;
    render();
  }

  function controlHtml(f) {
    const dis = S.set.exists ? '' : 'disabled';
    const v = curVal(f), k = esc(f.key);
    switch (f.type) {
      case 'toggle': return `<label class="switch"><input type="checkbox" data-k="${k}" ${v ? 'checked' : ''} ${dis}><span></span></label>`;
      case 'slider': {
        const lo = Math.min(f.min, v ?? f.min), hi = Math.max(f.max, v ?? f.min);
        return `<div class="slider"><input type="range" data-k="${k}" min="${lo}" max="${hi}" step="${f.step || 1}" value="${v ?? f.min}" style="--p:${fillPct(lo, hi, v ?? f.min)}%" ${dis}><output>${fmtSlider(f, v ?? f.min)}</output></div>`;
      }
      case 'number': return `<div class="num"><input type="number" data-k="${k}" value="${v ?? ''}" ${f.min != null ? `min="${f.min}"` : ''} step="${f.step || 1}" ${dis}>${f.unit ? `<span>${esc(f.unit)}</span>` : ''}</div>`;
      case 'select': {
        const opts = f.options.slice();
        if (!opts.some((o) => same(o.value, v))) opts.push({ value: v, label: `${v} (valore personalizzato)` });
        return `<select data-k="${k}" ${dis}>${opts.map((o) => `<option value="${esc(JSON.stringify(o.value))}" ${same(o.value, v) ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`;
      }
      case 'text': return `<input class="txt" type="text" data-k="${k}" value="${esc(v ?? '')}" placeholder="${esc(f.placeholder || '')}" spellcheck="false" ${dis}>`;
      default: { const val = S.set.saved[f.key]; return `<span class="info-val">${val === undefined ? '—' : esc(val)}</span>`; }
    }
  }

  function rowHtml(f) {
    return `<div class="row ${f.type === 'info' ? 'info' : ''} ${f.key in S.set.draft ? 'dirty' : ''}" data-row="${esc(f.key)}">
      <div class="row-text"><b>${esc(f.label)}</b>${f.desc ? `<span>${esc(f.desc)}</span>` : ''}</div>
      <div class="row-ctl">${controlHtml(f)}</div></div>`;
  }

  function renderSettings() {
    const st = S.set;
    if (!st) { $('#main').innerHTML = '<div class="main-inner"><div class="skeleton" style="height:240px"></div></div>'; return; }
    const groups = st.schema.groups;
    const group = groups.find((x) => x.id === st.tab) || groups[0];
    $('#main').innerHTML = `<div class="main-inner">
      <header class="set-head">
        <div><div class="hero-tag">${ic('sliders')} Impostazioni</div><h1>${esc((st.title || '').replace(/\s*\(.*\)$/, ''))}</h1>
          <div class="chips"><span class="chip" title="${esc(st.path)}">${ic('folder')}<b>${esc(st.path)}</b></span></div></div>
        ${st.hasBackup ? `<button class="btn btn-sm" data-act="set-restore">${ic('undo')} Ripristina l'originale</button>` : ''}
      </header>
      ${st.running ? `<div class="notice">${ic('warn')}<div><b>Il gioco è aperto</b>Chiudilo prima di salvare. Per cambiare le impostazioni mentre giochi premi ${esc(S.data.session && S.data.session.hotkey || hotkeyLabel())}: ${S.data.session && S.data.session.live ? 'molte si vedono subito nel gioco' : 'si applicheranno alla chiusura'}.</div>
        <button class="btn btn-sm" data-act="set-recheck">Ricontrolla</button></div>` : ''}
      ${Object.keys(st.pending).length ? `<div class="notice info">${ic('info')}<div><b>${Object.keys(st.pending).length} ${Object.keys(st.pending).length === 1 ? 'modifica in attesa' : 'modifiche in attesa'}</b>Si applicano appena il gioco si chiude. I valori qui sotto le includono già.</div>
        <button class="btn btn-sm" data-act="set-clear-pending">Scarta</button></div>` : ''}
      ${!st.exists ? `<div class="notice">${ic('info')}<div><b>Le impostazioni non esistono ancora</b>Avvia il gioco una volta (anche solo per qualche secondo), chiudilo e torna qui.</div>
        <button class="btn btn-sm" data-act="set-recheck">Ricontrolla</button></div>` : ''}
      ${st.schema.optimizer ? '<section class="opt-card" id="optimizer"></section>' : ''}
      <div class="stabs" role="tablist">${groups.map((x) => `<button class="stab ${x.id === group.id ? 'active' : ''}" role="tab" data-act="stab" data-id="${esc(x.id)}">${ic(x.icon || 'sliders')}${esc(x.title)}<i></i></button>`).join('')}</div>
      ${group.presets ? `<div class="presets"><span class="k">Profili rapidi</span>${group.presets.map((p, i) => `<button class="preset" data-act="preset" data-id="${i}" ${st.exists ? '' : 'disabled'}><b>${esc(p.name)}</b><small>${esc(p.desc || '')}</small></button>`).join('')}</div>` : ''}
      ${group.sections.map((s) => `<section class="set-card"><h3>${esc(s.title)}</h3>${s.note ? `<p class="note">${esc(s.note)}</p>` : ''}${s.fields.map(rowHtml).join('')}</section>`).join('')}
    </div>`;
    markTabs();
    renderOptimizer();
  }

  // ---- "Ottimizza per il tuo PC": one slider between performance and quality
  const shown = (f, v) => (f.type === 'toggle' ? (v ? 'Sì' : 'No')
    : f.type === 'select' ? ((f.options.find((o) => same(o.value, v)) || {}).label || String(v))
    : f.type === 'slider' ? fmtSlider(f, v) : `${v}${f.unit ? ` ${f.unit}` : ''}`);

  function optDiff(level) {
    const fields = Object.fromEntries(allFields().map((f) => [f.key, f]));
    return Object.entries(S.set.schema.optimizer.levels[level - 1].values)
      .filter(([k]) => fields[k]).map(([k, v]) => ({ f: fields[k], from: curVal(fields[k]), to: v }))
      .filter((d) => !same(d.from, d.to));
  }

  function closestLevel() {
    const fields = Object.fromEntries(allFields().map((f) => [f.key, f]));
    let best = 1, bestScore = -1;
    S.set.schema.optimizer.levels.forEach((L, i) => {
      const score = Object.entries(L.values).filter(([k, v]) => fields[k] && same(curVal(fields[k]), v)).length;
      if (score > bestScore) { bestScore = score; best = i + 1; }
    });
    return best;
  }

  function hwHtml(hw) {
    if (!hw) return '<span class="chip">Rilevo il tuo PC…</span>';
    if (hw.error) return `<span class="chip">Non riesco a rilevare l’hardware</span><button class="icon-btn" data-act="hw-refresh" title="Riprova">${ic('refresh')}</button>`;
    return `<span class="chip" title="Scheda video">${ic('monitor')}<b>${esc(hw.gpu.replace(/^NVIDIA |^AMD /, ''))}${hw.vramGb ? ` · ${hw.vramGb} GB` : ''}</b></span>
      <span class="chip" title="Processore">${ic('cpu')}<b>${esc(hw.cpu.replace(/\(R\)|\(TM\)|CPU|Processor/g, '').replace(/\s+/g, ' ').trim())}</b></span>
      ${hw.ramGb ? `<span class="chip" title="Memoria RAM">${ic('box')}<b>${Math.round(hw.ramGb)} GB RAM</b></span>` : ''}
      <span class="chip tier t${hw.tier}" title="${hw.recognised ? 'Stima in base alla scheda video' : 'Scheda non riconosciuta: stima prudente'}">Fascia <b>${esc(hw.tierName)}</b></span>
      <button class="icon-btn" data-act="hw-refresh" title="Rileva di nuovo">${ic('refresh')}</button>`;
  }

  function optDetailHtml() {
    const opt = S.set.schema.optimizer, lvl = S.optLevel, L = opt.levels[lvl - 1];
    const reco = S.hw && S.hw.recommended, diffs = optDiff(lvl), dis = S.set.exists ? '' : 'disabled';
    return `<div class="opt-level"><div class="opt-name"><b>${esc(L.name)}</b>${lvl === reco ? `<span class="reco-pill">${ic('check')} Consigliato per il tuo PC</span>` : ''}</div>
        <p>${esc(L.desc)}</p>
        <div class="diff">${diffs.length
          ? diffs.map((d) => `<span class="chg"><span>${esc(d.f.label)}</span><s>${esc(shown(d.f, d.from))}</s><i>→</i><b>${esc(shown(d.f, d.to))}</b></span>`).join('')
          : '<span class="same">Le impostazioni sono già così.</span>'}</div></div>
      <div class="opt-actions">
        ${reco && lvl !== reco ? `<button class="btn btn-sm" data-act="opt-set" data-id="${reco}">${ic('bolt')} Vai al consigliato: ${esc(opt.levels[reco - 1].name)}</button>` : ''}
        <button class="btn btn-green" data-act="opt-apply" ${diffs.length && !dis ? '' : 'disabled'}>${ic('check')} Applica «${esc(L.name)}»</button>
      </div>`;
  }

  function renderOptimizer() {
    const el = $('#optimizer');
    if (!el || !S.set || !S.set.schema.optimizer) return;
    const opt = S.set.schema.optimizer, n = opt.levels.length, reco = S.hw && S.hw.recommended;
    if (S.optLevel == null) S.optLevel = closestLevel();
    const lvl = S.optLevel, dis = S.set.exists ? '' : 'disabled';
    el.innerHTML = `
      <div class="opt-head">
        <div class="opt-title"><span class="opt-ic">${ic('bolt')}</span>
          <div><h3>Ottimizza per il tuo PC</h3><p>Scegli quanto spingere tra prestazioni e qualità: ModHub imposta tutto per te.</p></div></div>
        <div class="hw">${hwHtml(S.hw)}</div>
      </div>
      <div class="opt-scale">
        <span class="end">${ic('bolt')} Prestazioni</span>
        <div class="opt-track">
          <input type="range" id="optSlider" min="1" max="${n}" step="1" value="${lvl}" style="--p:${((lvl - 1) / (n - 1)) * 100}%" ${dis} aria-label="Da prestazioni a qualità">
          <div class="ticks">${opt.levels.map((L, i) => `<button class="tick ${i + 1 === lvl ? 'on' : ''} ${i + 1 === reco ? 'reco' : ''}" data-act="opt-set" data-id="${i + 1}" style="--f:${i / (n - 1)}" title="${esc(L.name)}" ${dis}><i></i>${i + 1 === reco ? '<em>Consigliato</em>' : ''}</button>`).join('')}</div>
        </div>
        <span class="end">Qualità ${ic('sparkles')}</span>
      </div>
      <div id="optDetail" class="opt-detail">${optDetailHtml()}</div>`;
  }

  function updateOptDetail() {
    const slider = $('#optSlider');
    if (!slider) return;
    const n = Number(slider.max), lvl = S.optLevel;
    slider.value = lvl;
    slider.style.setProperty('--p', `${((lvl - 1) / (n - 1)) * 100}%`);
    document.querySelectorAll('.tick').forEach((t, i) => t.classList.toggle('on', i + 1 === lvl));
    $('#optDetail').innerHTML = optDetailHtml();
  }

  function applyOptimizer() {
    const opt = S.set.schema.optimizer, L = opt.levels[S.optLevel - 1];
    const fields = Object.fromEntries(allFields().map((f) => [f.key, f]));
    for (const [k, v] of Object.entries(L.values)) if (fields[k]) setDraft(fields[k], v);
    renderMain();
    toast(`Livello «${L.name}» applicato. Premi Salva per confermare.`);
  }

  async function loadHardware(refresh = false) {
    if (!S.set || !S.set.schema.optimizer) return;
    const gid = settingsTarget();
    try { S.hw = await api(`hardware?game=${encodeURIComponent(gid)}${refresh ? '&refresh=1' : ''}`); }
    catch (e) { S.hw = { error: e.message }; }
    if (S.view === 'settings' && settingsTarget() === gid) { renderOptimizer(); }
  }

  function markTabs() {
    if (!S.set) return;
    S.set.schema.groups.forEach((g) => {
      const el = document.querySelector(`[data-act="stab"][data-id="${CSS.escape(g.id)}"]`);
      if (el) el.classList.toggle('dirty', fieldsOf(g).some((f) => f.key in S.set.draft));
    });
  }

  function renderSaveBar() {
    const bar = $('#savebar'), n = dirtyCount(), on = S.view === 'settings' && n > 0;
    bar.classList.toggle('on', on);
    if (!on) return;
    const blocked = S.set.running;  // a running game rewrites its settings on exit: use the in-game panel instead
    const hint = blocked ? 'Chiudi il gioco per poter salvare' : '';
    bar.innerHTML = `<span class="sb-text"><b>${n}</b> ${n === 1 ? 'modifica non salvata' : 'modifiche non salvate'}${hint ? `<small>${hint}</small>` : ''}</span>
      <button class="btn btn-sm" data-act="set-discard">${ic('undo')} Annulla</button>
      <button class="btn btn-green" data-act="set-save" ${blocked ? 'disabled' : ''}>${ic('save')} Salva</button>`;
  }

  function setDraft(f, value) {
    if (same(value, baseVal(f))) delete S.set.draft[f.key]; else S.set.draft[f.key] = value;
    const row = document.querySelector(`[data-row="${CSS.escape(f.key)}"]`);
    if (row) row.classList.toggle('dirty', f.key in S.set.draft);
    markTabs(); renderSaveBar(); renderSidebar();
  }

  function onControl(e) {
    const el = e.target, key = el.dataset && el.dataset.k;
    if (!key || !S.set) return;
    const f = allFields().find((x) => x.key === key);
    if (!f) return;
    let v;
    if (f.type === 'toggle') v = el.checked;
    else if (f.type === 'slider' || f.type === 'number') {
      v = parseFloat(el.value);
      if (Number.isNaN(v)) return;
      if (e.type === 'change' && f.type === 'number' && f.min != null && v < f.min) { v = f.min; el.value = v; }
      v = f.int ? Math.round(v) : Math.round(v * 10000) / 10000;
      if (f.type === 'slider') {
        el.style.setProperty('--p', `${fillPct(parseFloat(el.min), parseFloat(el.max), v)}%`);
        el.nextElementSibling.textContent = fmtSlider(f, v);
      }
    } else if (f.type === 'select') v = JSON.parse(el.value);
    else v = el.value;
    setDraft(f, v);
  }

  function applyPreset(i) {
    const group = S.set.schema.groups.find((x) => x.id === S.set.tab);
    const preset = group.presets[i];
    const byKey = Object.fromEntries(allFields().map((f) => [f.key, f]));
    for (const [k, v] of Object.entries(preset.values)) if (byKey[k]) setDraft(byKey[k], v);
    renderMain();
    toast(`Profilo «${preset.name}» applicato. Premi Salva per confermare.`);
  }

  async function saveSettings() {
    try {
      // queued changes are re-sent with the new ones so an explicit save never drops them
      const r = await api('settings-save', { game: settingsTarget(), changes: { ...S.set.pending, ...S.set.draft } });
      toast(r.queued ? 'Salvato: si applica quando chiudi il gioco.' : 'Impostazioni salvate.');
      await loadSettings(false);
    } catch (e) { toast(e.message, 'error'); if (e.status === 409) loadSettings(true); }
  }

  async function clearPending() {
    try { await api('settings-save', { game: settingsTarget(), changes: {}, queue: true }); toast('Modifiche in attesa scartate.'); await loadSettings(true); }
    catch (e) { toast(e.message, 'error'); }
  }

  async function restoreSettings() {
    const ok = await modal({ icon: 'undo', title: 'Ripristinare le impostazioni originali?', danger: true, ok: 'Ripristina',
      text: 'Il file torna com’era prima della prima modifica fatta da ModHub. Le modifiche salvate da allora andranno perse.' });
    if (!ok) return;
    try { await api('settings-restore', { game: settingsTarget() }); toast('Impostazioni originali ripristinate.'); await loadSettings(false); }
    catch (e) { toast(e.message, 'error'); }
  }

  // ---------------------------------------------------------------- events
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const id = t.dataset.id;
    switch (t.dataset.act) {
      case 'game': switchGame(id); break;
      case 'view': changeView(id); break;
      case 'stab': S.set.tab = id; renderMain(); $('#main').scrollTop = 0; break;
      case 'preset': applyPreset(Number(id)); break;
      case 'set-save': saveSettings(); break;
      case 'set-discard': S.set.draft = {}; S.optLevel = null; render(); break;
      case 'set-restore': restoreSettings(); break;
      case 'set-clear-pending': clearPending(); break;
      case 'emu-settings': openEmuSettings(id); break;
      case 'overlay-open': api('overlay-toggle', {}).catch((err) => toast(err.message, 'error')); break;
      case 'set-recheck': loadSettings(true); break;
      case 'opt-set': S.optLevel = Number(id); updateOptDetail(); break;
      case 'opt-apply': applyOptimizer(); break;
      case 'hw-refresh': S.hw = null; renderOptimizer(); loadHardware(true); break;
      case 'tab': S.tab = id; S.animate = true; renderMain(); break;
      case 'open': openDrawer(id); break;
      case 'close': closeDrawer(); break;
      case 'install': e.stopPropagation(); install(id); break;
      case 'install-sel': install(S.openId, S.version); break;
      case 'uninstall': uninstall(id); break;
      case 'play': play(); break;
      case 'folder': chooseFolder(); break;
      case 'add-game': addGameMenu(); break;
      case 'emu-add-game': addEmuGame(id || null); break;
      case 'emu-detect': case 'emu-pick': case 'emu-site': case 'emu-forget': emuAction(t.dataset.act, id); break;
      case 'emu-download': emuDownload(id); break;
      case 'emu-uninstall': emuUninstall(id); break;
      case 'emu-check': checkEmuUpdates(); break;
      case 'emu-open': api('emu-open', { emulator: id }).catch((err) => toast(err.message, 'error')); break;
      case 'emu-custom': addCustomEmulator(); break;
      case 'remove-game': removeGame(); break;
      case 'open-folder': api('open-folder', { game: S.gameId }).catch((err) => toast(err.message, 'error')); break;
      case 'source': changeSource(); break;
      case 'refresh': refresh(); break;
    }
  });
  $('#scrim').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && S.openId && !$('#modal').classList.contains('on')) closeDrawer();
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList && e.target.classList.contains('card')) { e.preventDefault(); openDrawer(e.target.dataset.id); }
  });
  document.addEventListener('input', (e) => {
    if (e.target.id === 'q') { S.q = e.target.value; renderGrid(); }
    else if (e.target.id === 'qe') { S.qe = e.target.value; $('#emuGrid').innerHTML = emuCards(); }
    else if (e.target.id === 'optSlider') { S.optLevel = Number(e.target.value); updateOptDetail(); }
    else onControl(e);
  });
  document.addEventListener('change', (e) => {
    if (e.target.id === 'hotkeySel') changeHotkey(e.target.value);
    else if (e.target.id === 'profileSel') S.profiles[S.gameId] = e.target.value;
    else if (e.target.id === 'verSel') { S.version = e.target.value; renderDrawer(); }
    else onControl(e);
  });
  addEventListener('focus', () => { if (S.view === 'settings' && S.set) loadSettings(true); });
  addEventListener('beforeunload', (e) => { if (dirtyCount()) { e.preventDefault(); e.returnValue = ''; } });

  setInterval(() => api('ping', {}).catch(() => {}), 5000);
  api('ping', {}).catch(() => {});
  setInterval(watchSession, 3000);
  addEventListener('pagehide', () => navigator.sendBeacon(`/api/bye?t=${encodeURIComponent(T)}`, '{}'));
  load();
})();
