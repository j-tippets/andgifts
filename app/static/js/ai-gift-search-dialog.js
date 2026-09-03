// "Ask AI for a gift idea" popup on the org-wide Gift Catalog page (see
// catalog/list.html + app.routes.catalog.ai_search). Deliberately
// separate from ai-gift-search.js (the per-contact Send a gift page's
// version): that one hides/reveals cards in the main grid, this one is
// a tasteful, out-of-the-way dialog that renders its own small results
// list with direct "Order this gift" links, since there's no specific
// contact yet to filter a per-contact grid for.
(function () {
  var dialog = document.getElementById('ai-gift-search-dialog');
  var trigger = document.getElementById('ai-gift-search-trigger');
  if (!dialog || !trigger) return;

  var form = document.getElementById('ai-gift-search-form');
  var input = document.getElementById('ai-gift-search-input');
  var btn = document.getElementById('ai-gift-search-btn');
  var status = document.getElementById('ai-gift-search-status');
  var closeBtn = document.getElementById('ai-gift-search-close');
  var resultsBox = document.getElementById('ai-gift-search-results');
  var messageEl = document.getElementById('ai-gift-search-message');
  var listEl = document.getElementById('ai-gift-search-list');

  trigger.addEventListener('click', function () {
    dialog.showModal();
    input.focus();
  });
  if (closeBtn) closeBtn.addEventListener('click', function () { dialog.close(); });
  // Native <dialog> already closes on a backdrop click via the browser's
  // own Esc handling; clicking the backdrop itself needs its own check
  // since a click anywhere in the (block-level) dialog element bubbles
  // up to it -- only close when the click target is the dialog itself,
  // not something inside it.
  dialog.addEventListener('click', function (e) {
    if (e.target === dialog) dialog.close();
  });

  function renderMatches(matches) {
    listEl.innerHTML = '';
    matches.forEach(function (m) {
      var row = document.createElement('div');
      row.className = 'ai-match-row';

      var info = document.createElement('div');
      var title = document.createElement('div');
      title.className = 'ai-match-row-title';
      title.textContent = m.name + ' \u2014 $' + (m.price_cents / 100).toFixed(2);
      info.appendChild(title);
      if (m.reasoning) {
        var reasoning = document.createElement('p');
        reasoning.className = 'ai-match-note';
        reasoning.textContent = m.reasoning;
        info.appendChild(reasoning);
      }
      row.appendChild(info);

      var orderLink = document.createElement('a');
      orderLink.href = m.order_url;
      orderLink.className = 'btn btn-primary btn-small';
      orderLink.textContent = 'Order this gift';
      row.appendChild(orderLink);

      listEl.appendChild(row);
    });
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var description = (input.value || '').trim();
    if (!description) {
      input.focus();
      return;
    }

    btn.disabled = true;
    status.style.display = '';
    resultsBox.style.display = 'none';

    fetch(form.dataset.action, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: new FormData(form)
    })
      .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
      .then(function (result) {
        if (!result.ok) {
          messageEl.textContent = result.data.error || "Couldn't search right now -- try again in a moment.";
          listEl.innerHTML = '';
          resultsBox.style.display = '';
          return;
        }

        var matches = result.data.matches || [];
        if (matches.length === 0) {
          messageEl.textContent = 'Nothing in your available catalog felt like a real fit for that -- try rephrasing.';
          listEl.innerHTML = '';
          resultsBox.style.display = '';
          return;
        }

        renderMatches(matches);
        messageEl.textContent = result.data.used_ai
          ? 'Here\u2019s what matched:'
          : 'AI search isn\u2019t configured right now -- these are keyword matches instead:';
        resultsBox.style.display = '';
      })
      .catch(function () {
        messageEl.textContent = "Couldn't search right now -- try again in a moment.";
        listEl.innerHTML = '';
        resultsBox.style.display = '';
      })
      .finally(function () {
        btn.disabled = false;
        status.style.display = 'none';
      });
  });
})();
