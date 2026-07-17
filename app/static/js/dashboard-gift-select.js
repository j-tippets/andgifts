// Today: when an agent swaps the gift on a suggestion via the "Change gift"
// dropdown (card view or list view), update the name/price shown on the card
// immediately so it's clear what will actually go out when they hit Approve.
// The dropdown itself submits via its `form="..."` attribute pointing at the
// real approve form elsewhere in the DOM -- this listener is purely visual.
(function () {
  document.addEventListener('change', function (e) {
    var select = e.target;
    if (!select.classList || !select.classList.contains('s-gift-select')) return;

    var option = select.options[select.selectedIndex];
    if (!option.value) return; // "Keep as shown" -- leave the display alone

    var box = select.closest('.s-gift-box');
    if (!box) return;

    var nameEl = box.querySelector('.s-gift-name');
    var priceEl = box.querySelector('.s-gift-price');
    if (nameEl) nameEl.textContent = option.getAttribute('data-name');
    if (priceEl) priceEl.textContent = '$' + option.getAttribute('data-price');
  });
})();
