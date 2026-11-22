// Dućan AI chat widget. Usage on the shop website:
// <script src="https://APP/static/chat-widget.js" data-api="https://APP" defer></script>
(function () {
  const script = document.currentScript;
  const api = (script && script.dataset.api ? script.dataset.api : "").replace(/\/$/, "");
  const langs = ["hr", "en", "de", "it"];
  const guess = (navigator.language || "hr").slice(0, 2);
  let lang = langs.includes(guess) ? guess : "hr";
  const css = `
    .dai-btn{position:fixed;right:16px;bottom:16px;z-index:9999;border:0;border-radius:24px;padding:12px 16px;background:#0f6b74;color:#fff;font:15px system-ui,sans-serif;cursor:pointer}
    .dai-box{position:fixed;right:16px;bottom:70px;z-index:9999;width:min(360px,calc(100vw - 32px));max-height:70vh;display:flex;flex-direction:column;background:#fff;color:#1d2322;border:1px solid #d8dedb;border-radius:12px;font:14px/1.45 system-ui,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.15)}
    .dai-box[hidden]{display:none}
    .dai-head{display:flex;gap:8px;align-items:center;padding:10px 12px;border-bottom:1px solid #d8dedb}
    .dai-head strong{flex:1}
    .dai-log{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
    .dai-msg{padding:8px 10px;border-radius:10px;white-space:pre-wrap;max-width:90%}
    .dai-bot{background:#eef4f3;align-self:flex-start}.dai-me{background:#0f6b74;color:#fff;align-self:flex-end}
    .dai-form{display:flex;gap:6px;padding:10px;border-top:1px solid #d8dedb}
    .dai-form input{flex:1;padding:8px;border:1px solid #d8dedb;border-radius:8px;font:inherit}
    .dai-form button{border:0;border-radius:8px;background:#0f6b74;color:#fff;padding:8px 12px;font:inherit;cursor:pointer}
    .dai-note{font-size:12px;color:#5f6b67;padding:0 12px 10px}`;
  const style = document.createElement("style"); style.textContent = css; document.head.appendChild(style);
  const btn = document.createElement("button"); btn.className = "dai-btn"; btn.type = "button"; btn.textContent = "Pitajte nas · Chat";
  const box = document.createElement("div"); box.className = "dai-box"; box.hidden = true;
  box.innerHTML = `<div class="dai-head"><strong>AI asistent</strong><select aria-label="Jezik">${langs.map(l => `<option value="${l}">${l.toUpperCase()}</option>`).join("")}</select><button type="button" aria-label="Zatvori">×</button></div>
    <div class="dai-log" aria-live="polite"></div>
    <form class="dai-form"><input maxlength="500" required aria-label="Poruka"><button type="submit">Pošalji</button></form>
    <div class="dai-note"></div>`;
  document.body.append(btn, box);
  const log = box.querySelector(".dai-log"), select = box.querySelector("select"), note = box.querySelector(".dai-note");
  select.value = lang;
  const add = (text, cls) => { const m = document.createElement("div"); m.className = "dai-msg " + cls; m.textContent = text; log.appendChild(m); log.scrollTop = log.scrollHeight; };
  const intro = async () => {
    log.textContent = "";
    try { const r = await fetch(`${api}/api/chat/uvod?language=${lang}`); const j = await r.json(); add(j.text, "dai-bot"); note.textContent = j.disclosure; }
    catch { note.textContent = "Chat trenutno nije dostupan."; }
  };
  btn.addEventListener("click", () => { box.hidden = !box.hidden; if (!box.hidden && !log.childElementCount) intro(); });
  box.querySelector(".dai-head button").addEventListener("click", () => { box.hidden = true; });
  select.addEventListener("change", () => { lang = select.value; intro(); });
  box.querySelector("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = box.querySelector("input"); const text = input.value.trim(); if (!text) return;
    input.value = ""; add(text, "dai-me");
    try {
      const r = await fetch(`${api}/api/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text, language: lang }) });
      if (!r.ok) throw new Error(r.status);
      const j = await r.json(); add(j.text, "dai-bot");
    } catch { add("Trenutno ne mogu odgovoriti. Molimo pitajte osoblje u dućanu.", "dai-bot"); }
  });
})();
