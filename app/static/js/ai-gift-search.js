// "Explain the situation" AI-assisted gift search on the per-contact
// Send a gift page (see orders/browse.html + app.routes.contacts.
// ai_search_gifts). Independent of catalog-filters.js's normal
// search/theme/occasion/price/lead filters -- submitting this hides
// everything except the LLM's picks; "Clear and show everything" just
// re-triggers the normal filters' own reset so both stay in sync
// rather than this file having its own separate idea of "show all".
(function () {
  var form = document.getElementById('ai-gift-search-form');
  if (!form) return;

  var input = document.getElementById('ai-gift-search-input');
  var btn = document.getElementById('ai-gift-search-btn');
  var status = document.getElementById('ai-gift-search-status');
  var resultsBox = document.getElementById('ai-gift-search-results');
  var messageEl = document.getElementById('ai-gift-search-message');
  var clearBtn = document.getElementById('ai-gift-search-clear');
  var filtersClearBtn = document.getElementById('filters-clear');
  var cards = Array.prototype.slice.call(document.querySelectorAll('#catalog-items .catalog-card'));

  function cardFor(itemId) {
    return cards.filter(function (c) { return c.dataset.itemId === itemId; })[0];
  }

  function clearNotes() {
    Array.prototype.slice.call(document.querySelectorAll('.ai-match-note')).forEach(function (n) {
      n.parentNode.removeChild(n);
    });
  }

  function showOnly(matches) {
    var matchedIds = matches.map(function (m) { return m.item_id; });
    cards.forEach(function (card) {
      card.style.display = matchedIds.indexOf(card.dataset.itemId) === -1 ? 'none' : '';
    });
    var noResults = document.getElementById('catalog-no-results');
    if (noResults) noResults.style.display = 'none';
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
    clearNotes();

    fetch(form.getAttribute('action'), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: new FormData(form)
    })
      .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
      .then(function (result) {
        if (!result.ok) {
          messageEl.textContent = result.data.error || "Couldn't search right now -- try again in a moment, or use the filters below.";
          resultsBox.style.display = '';
          return;
        }

        var matches = result.data.matches || [];
        if (matches.length === 0) {
          messageEl.textContent = "Nothing in the current catalog felt like a real fit for that -- try rephrasing, or browse with the filters below.";
          resultsBox.style.display = '';
          return;
        }

        showOnly(matches);
        matches.forEach(function (m) {
          var card = cardFor(m.item_id);
          if (!card || !m.reasoning) return;
          var body = card.querySelector('.catalog-card-body');
          if (!body) return;
          var note = document.createElement('p');
          note.className = 'ai-match-note';
          note.textContent = m.reasoning;
          body.insertBefore(note, body.firstChild);
        });

        messageEl.textContent = result.data.used_ai
          ? 'Showing ' + matches.length + ' match' + (matches.length === 1 ? '' : 'es') + ' for that.'
          : 'AI search isn\u2019t configured right now -- these are keyword matches instead.';
        resultsBox.style.display = '';
      })
      .catch(function () {
        messageEl.textContent = "Couldn't search right now -- try again in a moment, or use the filters below.";
        resultsBox.style.display = '';
      })
      .finally(function () {
        btn.disabled = false;
        status.style.display = 'none';
      });
  });

  if (clearBtn) {
    clearBtn.addEventListener('click', function () {
      clearNotes();
      resultsBox.style.display = 'none';
      input.value = '';
      // Reuse catalog-filters.js's own reset (search box, theme,
      // occasion, price, lead all back to defaults, every card shown)
      // instead of duplicating that logic here.
      if (filtersClearBtn) filtersClearBtn.click();
    });
  }
})();
