/* MissionPilot AI — browser code: language switch, task updates, Claude-style question popup,
   document generation & sharing, AI chat, voice (speech synthesis + recognition) and the guided tour. */
(function () {
  'use strict';
  const MP = window.MP || { strings: {}, locale: 'en', tts: ['en-GB'], dir: 'ltr' };
  const S = (k, p) => {
    let s = MP.strings[k] || k.split('.').pop().replace(/_/g, ' ');
    if (p) Object.keys(p).forEach(n => { s = s.split('{' + n + '}').join(p[n]); });
    return s;
  };
  const $ = (q, el) => (el || document).querySelector(q);
  const $$ = (q, el) => Array.from((el || document).querySelectorAll(q));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function post(url, data) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data || {}), credentials: 'same-origin' });
    let j = {};
    try { j = await r.json(); } catch (e) { /* ignore */ }
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }
  let toastTimer;
  function toast(msg, ms) {
    const t = $('#toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), ms || 2600);
  }

  // ------------------------------------------------------------------ app actions
  const MPapp = {
    async setLang(lang) {
      try { await post('/api/lang', { lang }); } catch (e) { /* public pages */ }
      const u = new URL(location.href);
      u.searchParams.delete('lang');
      location.href = u.pathname + (u.search ? u.search : '') + u.hash;
    },
    async setProgress(id, v, el) {
      try {
        const r = await post('/api/tasks/' + id, { progress: +v });
        toast(S('js.saved_progress', { pct: r.project_progress_label }));
        setTimeout(() => location.reload(), 700);
      } catch (e) { toast(e.message); }
    },
    async setStatus(id, status) {
      try { await post('/api/tasks/' + id, { status }); toast(S('js.saved')); setTimeout(() => location.reload(), 500); }
      catch (e) { toast(e.message); }
    },
    async setEnd(id, date) {
      try { await post('/api/tasks/' + id, { end_date: date }); toast(S('js.rescheduled')); setTimeout(() => location.reload(), 700); }
      catch (e) { toast(e.message); }
    },
    async setBudget(id, field, value) {
      try { await post('/api/budget/' + id, { [field]: value }); toast(S('js.saved')); setTimeout(() => location.reload(), 600); }
      catch (e) { toast(e.message); }
    },
    async setRisk(id, status) {
      try { await post('/api/risks/' + id, { status }); toast(S('js.saved')); setTimeout(() => location.reload(), 500); }
      catch (e) { toast(e.message); }
    },
    async setMember(id, data) {
      try { await post('/api/members/' + id, data); toast(S('js.saved')); }
      catch (e) { toast(e.message); }
    },
    async deleteProject(id) {
      if (!confirm(S('js.confirm_delete'))) return;
      try { await post('/api/projects/' + id + '/delete'); location.href = '/app/projects'; } catch (e) { toast(e.message); }
    },
    async resetDemo() {
      try { const r = await post('/api/demo/reset'); location.href = r.url; } catch (e) { toast(e.message); }
    },
    async translateProject() {
      toast(S('js.translating'), 8000);
      try {
        const r = await post('/api/projects/' + MP.pid + '/translate', { lang: MP.locale });
        toast(S('js.translated', { n: r.count }));
        setTimeout(() => location.reload(), 900);
      } catch (e) { toast(e.message, 5000); }
    },
    async generate(kind, type) {
      const lang = ($('#out-lang') || {}).value || MP.locale;
      const date = ($('#rep-date') || {}).value;
      const ai = ($('#use-ai') || {}).checked;
      toast(S('js.generating'), 20000);
      try {
        const r = await post('/api/projects/' + MP.pid + '/generate', { kind, type, lang, date, ai });
        location.href = r.url;
      } catch (e) { toast(e.message, 5000); }
    },
    async share(docId) {
      let r;
      try { r = await post('/api/docs/' + docId + '/share'); } catch (e) { toast(e.message); return; }
      const text = r.title + ' — ' + r.project;
      const back = document.createElement('div');
      back.className = 'modal-back';
      back.innerHTML = `<div class="qcard" role="dialog" aria-modal="true" aria-label="${esc(S('share.title'))}">
        <div class="qbody"><h3>${esc(S('share.title'))}</h3>
        <p class="muted small" style="margin-top:-6px">${esc(S('share.note'))}</p>
        <div class="opt-other"><input type="text" readonly value="${esc(r.url)}" id="share-url" aria-label="URL"><button class="btn primary" id="sh-copy">${esc(S('share.copy'))}</button></div>
        <div class="actions" style="margin-top:14px">
          <a class="btn" target="_blank" rel="noopener" href="https://wa.me/?text=${encodeURIComponent(text + ' ' + r.url)}">WhatsApp</a>
          <a class="btn" href="mailto:?subject=${encodeURIComponent(text)}&body=${encodeURIComponent(text + '\n\n' + r.url)}">${esc(S('share.email'))}</a>
          <a class="btn" target="_blank" rel="noopener" href="https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(r.url)}">LinkedIn</a>
          ${navigator.share ? `<button class="btn" id="sh-native">${esc(S('share.more'))}</button>` : ''}
          ${navigator.canShare ? `<button class="btn" id="sh-file">${esc(S('share.file'))}</button>` : ''}
        </div></div>
        <div class="qfoot"><button class="btn danger" id="sh-stop">${esc(S('share.stop'))}</button><button class="btn" id="sh-close">${esc(S('common.close'))}</button></div></div>`;
      document.body.appendChild(back);
      const close = () => back.remove();
      back.addEventListener('click', e => { if (e.target === back) close(); });
      $('#sh-close', back).onclick = close;
      $('#sh-copy', back).onclick = async () => {
        try { await navigator.clipboard.writeText(r.url); } catch (e) { $('#share-url', back).select(); document.execCommand('copy'); }
        toast(S('share.copied'));
      };
      if ($('#sh-native', back)) $('#sh-native', back).onclick = () => navigator.share({ title: text, text, url: r.url }).catch(() => {});
      if ($('#sh-file', back)) $('#sh-file', back).onclick = async () => {
        toast(S('js.preparing'), 8000);
        try {
          const blob = await (await fetch('/files/doc/' + docId + '.pdf')).blob();
          const file = new File([blob], (r.title || 'document') + '.pdf', { type: 'application/pdf' });
          if (navigator.canShare({ files: [file] })) await navigator.share({ files: [file], title: text });
          else toast(S('share.file_unsupported'));
        } catch (e) { /* cancelled */ }
      };
      $('#sh-stop', back).onclick = async () => { await post('/api/docs/' + docId + '/unshare'); toast(S('share.stopped')); close(); };
    },
    filterHelp(q) {
      q = q.toLowerCase();
      $$('.help-item').forEach(el => { el.style.display = el.textContent.toLowerCase().includes(q) ? '' : 'none'; });
    }
  };
  document.addEventListener('click', e => {
    const a = e.target.closest('a[data-dl]');
    if (a) toast(S('js.preparing'), 4000);
  });

  // ------------------------------------------------------------------ voice: speech synthesis + recognition
  const MPvoice = {
    voices: [],
    async load() {
      if (!('speechSynthesis' in window)) return [];
      this.voices = speechSynthesis.getVoices();
      if (!this.voices.length) {
        await new Promise(res => {
          const done = () => { this.voices = speechSynthesis.getVoices(); res(); };
          speechSynthesis.addEventListener('voiceschanged', done, { once: true });
          setTimeout(done, 1500);
        });
      }
      return this.voices;
    },
    pick() {
      const pref = MP.tour && MP.tour.voice_name;
      if (pref) { const v = this.voices.find(x => x.name === pref); if (v) return v; }
      for (const tag of MP.tts) {
        const low = tag.toLowerCase();
        const list = this.voices.filter(v => v.lang && v.lang.toLowerCase().replace('_', '-').startsWith(low));
        if (list.length) return list.find(v => /natural|online|google|neural/i.test(v.name)) || list[0];
      }
      return null;
    },
    available() { return 'speechSynthesis' in window && !!this.pick(); },
    speak(text, onEnd) {
      if (!('speechSynthesis' in window)) return false;
      const v = this.pick();
      if (!v) return false;
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.voice = v; u.lang = v.lang; u.rate = (MP.tour && MP.tour.rate) || 1;
      if (MPvoice.rateOverride) u.rate = MPvoice.rateOverride;
      let started = false;
      u.onstart = () => { started = true; document.dispatchEvent(new CustomEvent('mp-speaking', { detail: true })); };
      u.onend = u.onerror = () => { document.dispatchEvent(new CustomEvent('mp-speaking', { detail: false })); if (onEnd) onEnd(); };
      speechSynthesis.speak(u);
      setTimeout(() => { if (!started && !speechSynthesis.speaking) document.dispatchEvent(new CustomEvent('mp-voice-blocked')); }, 900);
      return true;
    },
    stop() { if ('speechSynthesis' in window) speechSynthesis.cancel(); },
    async test() {
      await this.load();
      const st = $('#voice-status');
      const ok = this.speak(S('voice.test_phrase'));
      if (st) st.textContent = ok ? (this.pick().name + ' (' + this.pick().lang + ')') : S('voice.unavailable');
    },
    async fillSettings() {
      await this.load();
      const sel = $('#voice_name');
      if (!sel) return;
      const cur = sel.dataset.current;
      const pre = MP.tts.map(x => x.split('-')[0].toLowerCase());
      this.voices.filter(v => pre.includes((v.lang || '').toLowerCase().split(/[-_]/)[0])).forEach(v => {
        const o = document.createElement('option');
        o.value = v.name; o.textContent = v.name + ' (' + v.lang + ')';
        if (v.name === cur) o.selected = true;
        sel.appendChild(o);
      });
      if (sel.options.length === 1) $('#voice-status').textContent = S('voice.unavailable');
    },
    rec: null,
    listen(btn, input, onFinal) {
      const R = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!R) { toast(S('voice.stt_unsupported'), 4000); return; }
      if (this.rec) { this.rec.stop(); return; }
      const rec = new R();
      rec.lang = MP.tts[0]; rec.interimResults = true; rec.continuous = false;
      const before = input.value ? input.value + ' ' : '';
      rec.onresult = e => {
        let txt = '';
        for (let i = 0; i < e.results.length; i++) txt += e.results[i][0].transcript;
        input.value = before + txt;
        input.dispatchEvent(new Event('input'));
      };
      rec.onerror = e => { toast(S('voice.stt_error') + ' (' + e.error + ')', 4000); };
      rec.onend = () => { btn.classList.remove('on'); this.rec = null; if (onFinal) onFinal(); };
      btn.classList.add('on');
      this.rec = rec;
      rec.start();
      toast(S('voice.listening'), 2500);
    }
  };

  // ------------------------------------------------------------------ new project: composer + question popup
  const MPnew = {
    data: null, answers: {}, step: 0,
    init() {
      const ta = $('#prompt'), send = $('#send');
      if (!ta) return;
      const upd = () => { send.disabled = ta.value.trim().length < 3; };
      ta.addEventListener('input', upd);
      ta.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !send.disabled) { e.preventDefault(); this.ask(); } });
      send.onclick = () => this.ask();
      $$('#examples button').forEach(b => b.onclick = () => { ta.value = b.dataset.ex; upd(); ta.focus(); });
      const mic = $('#new-mic');
      if (mic) mic.onclick = () => MPvoice.listen(mic, ta, upd);
      upd();
    },
    async ask() {
      const prompt = $('#prompt').value.trim();
      $('#send').disabled = true;
      $('#send').innerHTML = '<span class="spin"></span>';
      try {
        this.data = await post('/api/plan/questions', { prompt, lang: MP.locale });
      } catch (e) { toast(e.message, 4000); $('#send').disabled = false; $('#send').textContent = '➜'; return; }
      this.answers = { name: this.data.name, description: this.data.description, currency: this.data.currency, type: this.data.type, prompt };
      this.step = 0;
      this.render();
    },
    q() { return this.data.questions[this.step]; },
    answered(q) {
      if (q.kind === 'team') return true;
      const v = this.answers[q.id]; return v !== undefined && v !== null && v !== '';
    },
    teamRows(q) {
      const a = this.answers;
      const n = Math.max(1, Math.min(40, parseInt(a.team_size, 10) || 3));
      const sugg = (q.suggested && (q.suggested[a.type] || q.suggested.generic)) || [];
      a.members = (a.members || []).slice(0, n);
      while (a.members.length < n) a.members.push({ name: '', role: sugg[a.members.length] || 'dev' });
      return a.members.map((m, i) => `<div class="team-row"><span class="k">${i + 1}</span>
        <input type="text" data-mi="${i}" value="${esc(m.name)}" placeholder="${esc(S('q.member_name', { n: i + 1 }))}" aria-label="${esc(S('q.member_name', { n: i + 1 }))}">
        <select data-mr="${i}" aria-label="${esc(S('doc.c.role'))}">${q.roles.map(r => `<option value="${r.value}" ${r.value === m.role ? 'selected' : ''}>${esc(r.label)}</option>`).join('')}</select></div>`).join('')
        + `<p class="muted small">${esc(S('q.members_help'))}</p>`;
    },
    render() {
      const qs = this.data.questions, q = this.q(), a = this.answers;
      const chips = qs.map((x, i) => `<button class="qchip ${i === this.step ? 'on' : (this.answered(x) ? 'done' : '')}" data-go="${i}">${esc(x.header || ('Q' + (i + 1)))}${this.answered(x) && i !== this.step ? ' ✓' : ''}</button>`).join('');
      let opts = q.kind === 'team' ? this.teamRows(q) : q.options.map((o, i) => {
        const sel = String(a[q.id]) === String(o.value) && !a['_other_' + q.id];
        return `<button class="opt ${sel ? 'sel' : ''}" data-val="${esc(o.value)}"><span class="k">${i + 1}</span><span>${esc(o.label)}</span>${o.recommended ? `<span class="rec">${esc(S('q.recommended'))}</span>` : ''}</button>`;
      }).join('');
      if (q.allow_other) {
        const type = q.kind === 'date' ? 'date' : (q.kind === 'money' || q.kind === 'int') ? 'number' : 'text';
        const cur = q.kind === 'money' ? `<select id="q-cur" aria-label="${esc(S('q.currency'))}">${MP.currencies.map(c => `<option ${c === a.currency ? 'selected' : ''}>${c}</option>`).join('')}</select>` : '';
        const otherVal = a['_other_' + q.id] ? esc(a[q.id]) : '';
        opts += `<div class="opt ${a['_other_' + q.id] ? 'sel' : ''}" style="cursor:default"><span class="k">${q.options.length + 1}</span>
          <div class="opt-other" style="flex:1"><input id="q-other" type="${type}" min="${type === 'number' ? 0 : ''}" placeholder="${esc(S('q.other'))}${q.other_unit ? ' (' + esc(q.other_unit) + ')' : ''}" value="${otherVal}">${cur}</div></div>`;
      }
      const last = this.step === qs.length - 1;
      $('#qmodal').innerHTML = `<div class="modal-back"><div class="qcard" role="dialog" aria-modal="true" aria-labelledby="q-title">
        <div class="qbody" style="padding-bottom:0">
          <label class="f" for="q-name" style="margin-top:0">${esc(S('q.project_name'))}</label>
          <input id="q-name" type="text" value="${esc(a.name)}" style="width:100%">
        </div>
        <div class="qhead">${chips}</div>
        <div class="qbody"><h3 id="q-title">${esc(q.question)}</h3>${opts}</div>
        <div class="qfoot"><button class="btn" id="q-back" ${this.step === 0 ? 'disabled' : ''}>${esc(S('common.back'))}</button>
          <span class="muted small">${esc(S('q.step', { n: this.step + 1, total: qs.length }))}</span>
          <button class="btn primary" id="q-next" ${this.answered(q) ? '' : 'disabled'}>${esc(last ? S('q.create') : S('common.next'))}</button></div>
      </div></div>`;
      const root = $('#qmodal');
      $('#q-name', root).oninput = e => { a.name = e.target.value; };
      $$('.opt[data-val]', root).forEach(b => b.onclick = () => {
        a[q.id] = b.dataset.val; delete a['_other_' + q.id];
        this.render();
        setTimeout(() => this.next(), 180);
      });
      const other = $('#q-other', root);
      if (other) {
        other.oninput = () => {
          if (other.value) { a[q.id] = other.value; a['_other_' + q.id] = true; } else { delete a[q.id]; delete a['_other_' + q.id]; }
          $('#q-next', root).disabled = !this.answered(q);
          $$('.opt', root).forEach(o => o.classList.toggle('sel', o.contains(other) && !!other.value));
        };
        other.onkeydown = e => { if (e.key === 'Enter' && this.answered(q)) this.next(); };
      }
      const cur = $('#q-cur', root);
      if (cur) cur.onchange = () => { a.currency = cur.value; };
      $$('[data-mi]', root).forEach(inp => inp.oninput = () => { a.members[+inp.dataset.mi].name = inp.value; });
      $$('[data-mr]', root).forEach(sel => sel.onchange = () => { a.members[+sel.dataset.mr].role = sel.value; });
      $$('[data-go]', root).forEach(b => b.onclick = () => { this.step = +b.dataset.go; this.render(); });
      $('#q-back', root).onclick = () => { if (this.step > 0) { this.step--; this.render(); } };
      $('#q-next', root).onclick = () => this.next();
      root.onkeydown = e => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        const n = parseInt(e.key, 10);
        const btns = $$('.opt[data-val]', root);
        if (n >= 1 && n <= btns.length) btns[n - 1].click();
        if (e.key === 'Escape') $('#qmodal').innerHTML = '';
      };
      (q.kind === 'team' ? $('[data-mi]', root) : (other && a['_other_' + q.id] ? other : $('.opt.sel', root) || $('.opt', root) || root)).focus();
    },
    next() {
      const q = this.q();
      if (!this.answered(q)) return;
      if (this.step < this.data.questions.length - 1) {
        this.step++;
        // skip already-answered questions only when moving forward automatically
        this.render();
      } else this.create();
    },
    async create() {
      const a = this.answers;
      $('#qmodal').innerHTML = '';
      $('#composer').hidden = true;
      $('#gen').hidden = false;
      $('#gen-name').textContent = a.name;
      const steps = $$('#gen-steps li');
      let i = 0, done = false, result = null, err = null;
      const tick = () => {
        if (i > 0) { steps[i - 1].className = 'ok'; steps[i - 1].querySelector('.ic').textContent = '✓'; }
        if (i < steps.length) { steps[i].className = 'on'; steps[i].querySelector('.ic').innerHTML = '<span class="spin"></span>'; i++; }
      };
      tick();
      const timer = setInterval(() => { if (i < steps.length - 1 || done) tick(); if (done && i >= steps.length) finish(); }, 520);
      const finish = () => {
        clearInterval(timer);
        steps.forEach(s => { s.className = 'ok'; s.querySelector('.ic').textContent = '✓'; });
        if (result) setTimeout(() => { location.href = result.url; }, 450);
      };
      const payload = {
        name: a.name, description: a.description || a.prompt, type: a.type, budget: a.budget, currency: a.currency,
        team_size: a.team_size, members: a.members || [], duration_weeks: a.duration_weeks, start_date: a.start_date,
        lang: MP.locale
      };
      Object.keys(a).forEach(k => { if (/^x\d$/.test(k)) payload[k] = a[k]; });
      try { result = await post('/api/plan/create', payload); } catch (e) { err = e; }
      done = true;
      if (err) { clearInterval(timer); toast(err.message, 6000); $('#gen').hidden = true; $('#composer').hidden = false; }
    }
  };

  // ------------------------------------------------------------------ AI assistant chat
  const MPchat = {
    init() {
      const input = $('#chat-input'), send = $('#chat-send'), box = $('#chat');
      if (!input) return;
      box.scrollTop = box.scrollHeight;
      const go = async () => {
        const msg = input.value.trim();
        if (!msg) return;
        input.value = '';
        box.insertAdjacentHTML('beforeend', `<div class="msg user" dir="auto">${esc(msg)}</div><div class="msg assistant" id="typing"><span class="speaking"><i></i><i></i><i></i></span></div>`);
        box.scrollTop = box.scrollHeight;
        try {
          const r = await post('/api/projects/' + MP.pid + '/chat', { message: msg, lang: ($('#out-lang') || {}).value || MP.locale });
          $('#typing').outerHTML = `<div class="msg assistant" dir="${r.dir}" lang="${r.lang}">${esc(r.reply)}</div>`;
        } catch (e) { $('#typing').outerHTML = `<div class="msg assistant">${esc(e.message)}</div>`; }
        box.scrollTop = box.scrollHeight;
      };
      send.onclick = go;
      input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); go(); } });
      $$('[data-ask]').forEach(b => b.onclick = () => { input.value = b.dataset.ask; go(); });
      const mic = $('#chat-mic');
      if (mic) mic.onclick = () => MPvoice.listen(mic, input);
    }
  };

  // ------------------------------------------------------------------ guided tour (spotlight + voice narration)
  const STEPS = [
    { page: null, sel: null }, { page: 'home', sel: '#nav' }, { page: 'home', sel: '#lang-switch' },
    { page: 'home', sel: '#btn-new-project' }, { page: 'home', sel: '#kpis' }, { page: 'tasks', sel: '#task-table' },
    { page: 'gantt', sel: '#gantt' }, { page: 'progress', sel: '#progress-chart' }, { page: 'budget', sel: '#budget-chart' },
    { page: 'risks', sel: '#risk-matrix' }, { page: 'team', sel: '#resource-chart' }, { page: 'reports', sel: '#report-list' },
    { page: 'documents', sel: '#doc-list' }, { page: 'assistant', sel: '#chat-input' }, { page: null, sel: '#tour-btn' }
  ];
  const MPtour = {
    i: 0, voice: true, muted: false, el: null,
    pageUrl(page) { return MP.pid ? '/app/p/' + MP.pid + '/' + page : '/app/new'; },
    welcome(startAt) {
      const w = document.createElement('div');
      w.className = 'welcome';
      w.innerHTML = `<div class="box" role="dialog" aria-modal="true"><div style="font-size:38px">✈</div><h3>${esc(S('tour.welcome_t'))}</h3>
        <p class="muted">${esc(S('tour.welcome_d'))}</p><div class="row">
        <button class="btn primary" id="tw-voice">🔊 ${esc(S('tour.start_voice'))}</button>
        <button class="btn" id="tw-text">${esc(S('tour.start_text'))}</button>
        <button class="btn ghost" id="tw-skip">${esc(S('tour.skip'))}</button></div></div>`;
      document.body.appendChild(w);
      $('#tw-voice', w).onclick = async () => { w.remove(); this.voice = true; await MPvoice.load(); this.go(startAt || 1); };
      $('#tw-text', w).onclick = () => { w.remove(); this.voice = false; this.go(startAt || 1); };
      $('#tw-skip', w).onclick = () => { w.remove(); this.finish(); };
      $('#tw-voice', w).focus();
    },
    start(step, manual) {
      if (!MP.tour) return;
      this.welcome(step || 1);
    },
    async go(i) {
      this.i = i;
      const st = STEPS[i];
      if (!st) return this.finish();
      if (st.page && MP.page !== st.page) {
        if (!MP.pid && st.page !== 'home') return this.go(i + 1);
        post('/api/tour', { step: i }).catch(() => {});
        location.href = this.pageUrl(st.page) + '?tour=' + i + '&tv=' + (this.voice ? 1 : 0);
        return;
      }
      post('/api/tour', { step: i }).catch(() => {});
      this.show();
    },
    clear() { $$('.tour-hole,.tour-tip').forEach(e => e.remove()); },
    show() {
      this.clear();
      const st = STEPS[this.i];
      const target = st.sel ? $(st.sel) : null;
      if (target) target.scrollIntoView({ block: 'center', behavior: 'instant' in window ? 'instant' : 'auto' });
      const hole = document.createElement('div');
      hole.className = 'tour-hole';
      const tip = document.createElement('div');
      tip.className = 'tour-tip';
      tip.setAttribute('role', 'dialog');
      tip.setAttribute('aria-live', 'polite');
      const title = S('tour.s' + this.i + '_t'), desc = S('tour.s' + this.i + '_d');
      const hasVoice = this.voice && MPvoice.available();
      tip.innerHTML = `<div class="step">${esc(S('tour.progress', { n: this.i, total: STEPS.length - 1 }))} <span id="tour-eq"></span></div>
        <h4>${esc(title)}</h4><p>${esc(desc)}</p>
        ${this.voice && !MPvoice.available() ? `<div class="vnote">${esc(S('voice.unavailable_tour'))}</div>` : ''}
        <div class="vnote" id="tour-blocked" hidden>${esc(S('voice.blocked'))}</div>
        ${hasVoice ? `<div class="ctl"><button class="btn sm" id="t-play">⏯ ${esc(S('tour.pause'))}</button><button class="btn sm" id="t-replay">↺ ${esc(S('tour.replay'))}</button>
          <button class="btn sm" id="t-mute">${this.muted ? '🔈' : '🔇'} ${esc(this.muted ? S('tour.unmute') : S('tour.mute'))}</button><button class="btn sm" id="t-rate">${(MPvoice.rateOverride || (MP.tour.rate || 1))}×</button></div>` : ''}
        <div class="row"><button class="btn sm ghost" id="t-skip">${esc(S('tour.skip'))}</button><span class="grow"></span>
        <button class="btn sm" id="t-back" ${this.i <= 1 ? 'disabled' : ''}>${esc(S('common.back'))}</button>
        <button class="btn sm primary" id="t-next">${esc(this.i === STEPS.length - 1 ? S('tour.finish') : S('common.next'))}</button></div>`;
      document.body.appendChild(hole);
      document.body.appendChild(tip);
      const place = () => {
        const vw = innerWidth, vh = innerHeight;
        if (target) {
          const r = target.getBoundingClientRect();
          const pad = 6;
          const top = Math.max(4, r.top - pad), left = Math.max(4, r.left - pad);
          const h = Math.min(r.height + pad * 2, vh - top - 4), w = Math.min(r.width + pad * 2, vw - left - 4);
          Object.assign(hole.style, { top: top + 'px', left: left + 'px', width: w + 'px', height: h + 'px' });
          const tw = tip.offsetWidth, th = tip.offsetHeight;
          let tt = top + h + 10, tl = Math.min(Math.max(8, left), vw - tw - 8);
          if (tt + th > vh - 8) tt = top - th - 10;
          if (tt < 8) { tt = Math.min(vh - th - 8, Math.max(8, top + 10)); tl = Math.max(8, Math.min(left + w + 10, vw - tw - 8)); }
          Object.assign(tip.style, { top: tt + 'px', left: tl + 'px' });
        } else {
          Object.assign(hole.style, { top: (vh / 2) + 'px', left: (vw / 2) + 'px', width: '0px', height: '0px' });
          Object.assign(tip.style, { top: Math.max(8, vh / 2 - tip.offsetHeight / 2) + 'px', left: Math.max(8, vw / 2 - tip.offsetWidth / 2) + 'px' });
        }
      };
      place();
      this._place = place;
      window.addEventListener('resize', place);
      $('#t-next', tip).onclick = () => { MPvoice.stop(); this.go(this.i + 1); };
      $('#t-back', tip).onclick = () => { MPvoice.stop(); this.go(this.i - 1); };
      $('#t-skip', tip).onclick = () => { MPvoice.stop(); this.finish(); };
      const say = () => { if (!this.muted && hasVoice) MPvoice.speak(title + '. ' + desc); };
      if (hasVoice) {
        let paused = false;
        $('#t-play', tip).onclick = () => {
          if (!speechSynthesis.speaking && !paused) { say(); return; }
          paused = !paused; paused ? speechSynthesis.pause() : speechSynthesis.resume();
        };
        $('#t-replay', tip).onclick = () => { $('#tour-blocked').hidden = true; say(); };
        $('#t-mute', tip).onclick = () => { this.muted = !this.muted; if (this.muted) MPvoice.stop(); this.show(); };
        $('#t-rate', tip).onclick = () => {
          const rates = [0.75, 1, 1.25];
          const cur = MPvoice.rateOverride || MP.tour.rate || 1;
          MPvoice.rateOverride = rates[(rates.indexOf(cur) + 1) % rates.length] || 1;
          $('#t-rate', tip).textContent = MPvoice.rateOverride + '×';
          say();
        };
      }
      tip.onkeydown = e => { if (e.key === 'Escape') { MPvoice.stop(); this.finish(); } if (e.key === 'ArrowRight') $('#t-next', tip).click(); };
      $('#t-next', tip).focus();
      say();
    },
    finish() {
      this.clear();
      window.removeEventListener('resize', this._place || (() => {}));
      post('/api/tour', { done: true }).catch(() => {});
      if (MP.tour) MP.tour.autostart = false;
      const u = new URL(location.href);
      if (u.searchParams.has('tour')) { u.searchParams.delete('tour'); u.searchParams.delete('tv'); history.replaceState(null, '', u.pathname + u.search); }
    }
  };
  document.addEventListener('mp-speaking', e => { const eq = $('#tour-eq'); if (eq) eq.innerHTML = e.detail ? '<span class="speaking"><i></i><i></i><i></i></span>' : ''; });
  document.addEventListener('mp-voice-blocked', () => { const b = $('#tour-blocked'); if (b) b.hidden = false; });

  // resume or auto-start the tour
  window.addEventListener('load', async () => {
    if (!MP.tour) return;
    const u = new URL(location.href);
    const step = parseInt(u.searchParams.get('tour') || '', 10);
    if (step > 1 && step < STEPS.length) {
      MPtour.voice = u.searchParams.get('tv') !== '0';
      if (MPtour.voice) await MPvoice.load();
      MPtour.go(step);
    } else if (MP.tour.autostart && !MP.tour.done) {
      MPtour.start(1);
    }
  });


  // ------------------------------------------------------------------ burndown day picker
  const MPburn = {
    init() {
      const D = window.MPBD, svg = $('#bd-svg');
      if (!D || !svg || !D.days.length) return;
      const n = D.days.length, slider = $('#bd-day'), num = $('#bd-daynum'), date = $('#bd-date');
      date.min = D.days[0].date; date.max = D.days[n - 1].date;
      const g = k => parseFloat(svg.dataset[k]);
      const X = i => { const x = g('l') + g('pw') * i / Math.max(1, n - 1); return svg.dataset.rtl === '1' ? g('w') - x : x; };
      const Y = v => g('t') + g('ph') * (1 - v / g('top'));
      const fmt = v => new Intl.NumberFormat(MP.locale, { maximumFractionDigits: 1 }).format(v);
      const pct = v => new Intl.NumberFormat(MP.locale, { style: 'percent', maximumFractionDigits: 0 }).format(v / 100);
      const set = day => {
        day = Math.max(1, Math.min(n, parseInt(day, 10) || 1));
        slider.value = day; num.value = day; const d = D.days[day - 1]; date.value = d.date;
        const x = X(day - 1);
        $('#bd-marker').style.display = '';
        $('#bd-line').setAttribute('x1', x); $('#bd-line').setAttribute('x2', x);
        const dp = $('#bd-dot-plan'), da = $('#bd-dot-act'), lb = $('#bd-label');
        dp.setAttribute('cx', x); dp.setAttribute('cy', Y(d.plan_left));
        if (d.act_left !== null) { da.style.display = ''; da.setAttribute('cx', x); da.setAttribute('cy', Y(d.act_left)); } else da.style.display = 'none';
        lb.textContent = S('progress.day_n', { n: day });
        const bw = lb.getComputedTextLength ? lb.getComputedTextLength() + 12 : 60;
        const ly = g('t') + g('ph') - 10;
        lb.setAttribute('x', x); lb.setAttribute('y', ly + 4);
        const bg = $('#bd-label-bg'); bg.setAttribute('x', x - bw / 2); bg.setAttribute('y', ly - 9); bg.setAttribute('width', bw);
        const past = d.act_left !== null;
        const plannedDone = D.tasks.filter(t => t.plan_day <= day);
        const actualDone = D.tasks.filter(t => t.done_day && t.done_day <= day);
        const doneList = past ? actualDone : plannedDone;
        const leftList = D.tasks.filter(t => !doneList.includes(t)).sort((a, b) => a.plan_day - b.plan_day);
        const planPct = 100 * (1 - d.plan_left / D.total), actPct = past ? 100 * (1 - d.act_left / D.total) : null;
        $('#bd-summary').innerHTML = `
          <div class="kpi"><div class="l">${esc(S('progress.bd_chosen'))}</div><div class="v">${esc(S('progress.day_n', { n: day }))}</div><div class="s">${esc(d.label)}${day === D.today ? ' · ' + esc(S('gantt.today')) : ''}</div></div>
          <div class="kpi"><div class="l">${esc(S('progress.bd_plan_by'))}</div><div class="v">${pct(planPct)}</div><div class="s">${esc(S('progress.bd_tasks_n', { n: plannedDone.length, total: D.tasks.length }))} · ${esc(S('progress.bd_left_days', { v: fmt(d.plan_left) }))}</div></div>
          <div class="kpi ${past ? (actPct + 2 >= planPct ? 'good' : 'bad') : ''}"><div class="l">${esc(S('progress.bd_actual_by'))}</div><div class="v">${past ? pct(actPct) : '—'}</div><div class="s">${past ? esc(S('progress.bd_tasks_n', { n: actualDone.length, total: D.tasks.length })) + ' · ' + esc(S('progress.bd_left_days', { v: fmt(d.act_left) })) : esc(S('progress.bd_future'))}</div></div>`;
        $('#bd-done-h').textContent = (past ? S('progress.bd_done_by', { n: day }) : S('progress.bd_will_done_by', { n: day })) + ' (' + doneList.length + ')';
        $('#bd-left-h').textContent = S('progress.bd_after', { n: day }) + ' (' + leftList.length + ')';
        const li = (t, cls, when) => `<li class="${cls}"><span class="pdot p-${t.prio}"></span><span>${esc(t.title)}</span><span class="when">${esc(when)}</span></li>`;
        $('#bd-done').innerHTML = doneList.sort((a, b) => (a.done_day || a.plan_day) - (b.done_day || b.plan_day))
          .map(t => li(t, past ? 'done' : 'plan', S('progress.day_n', { n: past ? t.done_day : t.plan_day }) + ' · ' + t.who)).join('') || `<li class="muted">${esc(S('report.none'))}</li>`;
        $('#bd-left').innerHTML = leftList.map(t => li(t, (t.plan_day <= day) ? 'late' : '', S('progress.bd_planned_day', { n: t.plan_day }) + ' · ' + t.who + (t.progress ? ' · ' + pct(t.progress) : ''))).join('') || `<li class="muted">${esc(S('report.none'))}</li>`;
      };
      slider.oninput = () => set(slider.value);
      num.onchange = () => set(num.value);
      date.onchange = () => {
        const v = date.value; let i = D.days.findIndex(x => x.date >= v); if (i < 0) i = n - 1; set(i + 1);
      };
      $('#bd-today').onclick = () => set(D.today || 1);
      set(slider.value);
    }
  };

  window.MPburn = MPburn;
  window.MPapp = MPapp; window.MPnew = MPnew; window.MPchat = MPchat; window.MPvoice = MPvoice; window.MPtour = MPtour;
})();
