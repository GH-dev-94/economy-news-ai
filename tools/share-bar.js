(function () {
  if (document.getElementById("zippy-universal-share-bar")) return;

  var style = document.createElement("style");
  style.textContent = [
    ".zippy-share-bar{display:flex;gap:0.5rem;flex-wrap:wrap;margin:0.85rem 0 1rem;}",
    ".zippy-share-btn{flex:1;min-width:140px;background:#0b0e14;border:1px solid rgba(250,250,250,0.16);color:#fafafa;font-size:0.83rem;font-weight:600;padding:0.58rem 0.8rem;border-radius:8px;cursor:pointer;font-family:inherit;transition:border-color .15s,background .15s;white-space:nowrap;}",
    ".zippy-share-btn:hover{border-color:#3b82f6;background:rgba(59,130,246,0.12);}"
  ].join("");
  document.head.appendChild(style);

  function buildSummaryText() {
    var h1 = document.querySelector(".page-header h1");
    var title = h1 ? h1.textContent.trim() : document.title;
    var lines = ["[ZIPPY 금융계산기] " + title];

    var hero = document.querySelector(".result-hero");
    if (hero) {
      var big = hero.querySelector(".dsr-big-num, .hero-big, .big-val");
      if (big && big.textContent.trim()) {
        lines.push("▶ 핵심 결과: " + big.textContent.trim());
      }
    }
    document.querySelectorAll(".kpi-card").forEach(function (card) {
      var lbl = card.querySelector(".kpi-label");
      var val = card.querySelector(".kpi-val");
      if (lbl && val) {
        lines.push("• " + lbl.textContent.trim() + ": " + val.textContent.trim());
      }
    });
    lines.push("👉 직접 계산해보기: " + window.location.origin + window.location.pathname);
    return lines.join("\n");
  }

  function flashBtn(btn, msg) {
    var orig = btn.textContent;
    btn.textContent = msg;
    setTimeout(function () { btn.textContent = orig; }, 1800);
  }

  var existingActionBar = document.querySelector(".action-bar");
  var bar = document.createElement("div");
  bar.id = "zippy-universal-share-bar";
  bar.className = "zippy-share-bar";

  var copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "zippy-share-btn";
  copyBtn.textContent = "📋 결과 요약 복사 (출처 포함)";
  copyBtn.onclick = function () {
    var text = buildSummaryText();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        flashBtn(copyBtn, "✅ 복사 완료!");
      }).catch(function () {});
    }
  };

  var shareBtn = document.createElement("button");
  shareBtn.type = "button";
  shareBtn.className = "zippy-share-btn";
  shareBtn.textContent = "🔗 계산기 링크 공유";
  shareBtn.onclick = function () {
    var url = window.location.origin + window.location.pathname;
    var h1 = document.querySelector(".page-header h1");
    var title = h1 ? h1.textContent.trim() : "ZIPPY 금융계산기";
    if (navigator.share) {
      navigator.share({ title: title, text: buildSummaryText(), url: url }).catch(function () {});
    } else if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () {
        flashBtn(shareBtn, "✅ 링크 복사됨!");
      }).catch(function () {});
    }
  };

  bar.appendChild(copyBtn);
  bar.appendChild(shareBtn);

  if (existingActionBar) {
    // 기존 .action-bar가 있으면 그 바로 아래에 출처포함/공유 버튼을 추가하지 않고 기존 바가 없는 페이지에만 삽입하거나,
    // 기존 바에 없는 페이지에만 깔끔하게 부착
    return;
  }

  var kpiGrid = document.querySelector(".kpi-grid");
  var resultHero = document.querySelector(".result-hero");
  var anchor = kpiGrid || resultHero;
  if (anchor && anchor.parentNode) {
    anchor.parentNode.insertBefore(bar, anchor.nextSibling);
  }
})();
