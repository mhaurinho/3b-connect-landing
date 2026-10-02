(function () {
  var RECEITA = document.body.getAttribute("data-receita") || "receita";
  var KEY_OS = "3b-os", KEY_DONE = "3b-" + RECEITA + "-done";
  function load(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } }
  function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

  // Sistema operacional
  function guessOS() {
    var p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    if (/mac/i.test(p)) return "mac";
    if (/linux/i.test(p) && !/android/i.test(navigator.userAgent)) return "linux";
    return "win";
  }
  function setOS(os) {
    document.querySelectorAll("[data-os]").forEach(function (el) {
      el.classList.toggle("os-on", el.getAttribute("data-os").split(" ").indexOf(os) !== -1);
    });
    document.querySelectorAll("[data-set-os]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-set-os") === os));
    });
    save(KEY_OS, os);
  }
  document.querySelectorAll("[data-set-os]").forEach(function (b) {
    b.addEventListener("click", function () { setOS(b.getAttribute("data-set-os")); });
  });
  setOS(load(KEY_OS, guessOS()));

  // Copiar
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var text = btn.parentNode.querySelector("pre").textContent;
      function ok() { btn.textContent = "Copiado ✓"; btn.classList.add("ok"); setTimeout(function () { btn.textContent = "Copiar"; btn.classList.remove("ok"); }, 1600); }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(ok, function () { fallback(text); ok(); });
      } else { fallback(text); ok(); }
    });
  });
  function fallback(text) {
    var t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); } catch (e) {} document.body.removeChild(t);
  }

  // Progresso
  var done = load(KEY_DONE, {});
  var boxes = document.querySelectorAll("[data-mission]");
  var total = 0;
  boxes.forEach(function (b) { if (b.getAttribute("data-mission") !== "0") total++; });
  function paint() {
    var n = 0;
    boxes.forEach(function (b) {
      var m = b.getAttribute("data-mission");
      b.checked = !!done[m];
      document.getElementById("m" + m).classList.toggle("done", !!done[m]);
      if (done[m] && m !== "0") n++;
    });
    var barra = document.getElementById("bar"), rotulo = document.getElementById("barLabel");
    if (barra) barra.style.width = (total ? n / total * 100 : 0) + "%";
    if (rotulo) rotulo.textContent = n + " de " + total;
  }
  boxes.forEach(function (b) {
    b.addEventListener("change", function () { done[b.getAttribute("data-mission")] = b.checked; save(KEY_DONE, done); paint(); });
  });
  paint();
})();
