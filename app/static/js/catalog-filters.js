// Catalog search/theme/price/lead-time filtering -- shared by the
// org-wide Gift Catalog page and the per-contact "Send a gift" browse
// page (see app/templates/catalog/_macros.html for the markup both
// render). The include/exclude chips only exist on the org-wide page
// (an agency admin managing which items are available at all), so
// every chip lookup here is defensive -- this runs unchanged on a
// page with no chips at all.
(function () {
  var input = document.getElementById('catalog-search');
  var chips = Array.prototype.slice.call(document.querySelectorAll('.chip[data-filter]'));
  var cards = Array.prototype.slice.call(document.querySelectorAll('#catalog-items .catalog-card'));
  var noResults = document.getElementById('catalog-no-results');
  var activeFilter = 'all';

  var themeSelect = document.getElementById('filter-theme');
  var occasionSelect = document.getElementById('filter-occasion');
  var priceRange = document.getElementById('filter-price');
  var priceLabel = document.getElementById('filter-price-label');
  var leadRange = document.getElementById('filter-lead');
  var leadLabel = document.getElementById('filter-lead-label');
  var sortSelect = document.getElementById('filter-sort');
  var cardsContainer = cards.length ? cards[0].parentElement : null;

  function applySort() {
    if (!sortSelect || !cardsContainer) return;
    var sorted = cards.slice().sort(function (a, b) {
      switch (sortSelect.value) {
        case 'price-desc':
          return parseInt(b.dataset.price, 10) - parseInt(a.dataset.price, 10);
        case 'name-asc':
          return a.dataset.name < b.dataset.name ? -1 : a.dataset.name > b.dataset.name ? 1 : 0;
        case 'price-asc':
        default:
          return parseInt(a.dataset.price, 10) - parseInt(b.dataset.price, 10);
      }
    });
    sorted.forEach(function (card) { cardsContainer.appendChild(card); });
  }

  function apply() {
    var q = (input && input.value.trim().toLowerCase()) || '';
    var theme = (themeSelect && themeSelect.value) || '';
    var occasion = (occasionSelect && occasionSelect.value) || '';
    var maxPrice = priceRange ? parseInt(priceRange.value, 10) : null;
    var maxLead = leadRange ? parseInt(leadRange.value, 10) : null;
    var visibleCount = 0;

    cards.forEach(function (card) {
      var matchesSearch = !q || card.dataset.search.indexOf(q) !== -1;
      var matchesFilter = activeFilter === 'all' || card.dataset.status === activeFilter;
      var matchesTheme = !theme || card.dataset.tags.split('|').indexOf(theme) !== -1;
      var matchesOccasion = !occasion || card.dataset.occasion === occasion;
      var matchesPrice = maxPrice === null || parseInt(card.dataset.price, 10) <= maxPrice;
      var matchesLead = maxLead === null || parseInt(card.dataset.lead, 10) <= maxLead;
      var visible = matchesSearch && matchesFilter && matchesTheme && matchesOccasion && matchesPrice && matchesLead;
      card.style.display = visible ? '' : 'none';
      if (visible) visibleCount++;
    });
    if (noResults) noResults.style.display = visibleCount === 0 ? '' : 'none';
  }

  if (input) input.addEventListener('input', apply);
  if (themeSelect) themeSelect.addEventListener('change', apply);
  if (occasionSelect) occasionSelect.addEventListener('change', apply);
  if (priceRange) {
    priceRange.addEventListener('input', function () {
      if (priceLabel) priceLabel.textContent = '$' + priceRange.value;
      apply();
    });
  }
  if (leadRange) {
    leadRange.addEventListener('input', function () {
      if (leadLabel) leadLabel.textContent = leadRange.value + ' days';
      apply();
    });
  }

  if (sortSelect) sortSelect.addEventListener('change', applySort);

  chips.forEach(function (chip) {
    chip.addEventListener('click', function (e) {
      e.preventDefault();
      chips.forEach(function (c) { c.classList.remove('active'); });
      chip.classList.add('active');
      activeFilter = chip.dataset.filter;
      apply();
    });
  });

  apply();
  applySort();
})();
