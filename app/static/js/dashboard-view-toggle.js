// Today: toggles between the swipeable card stack and the plain list view.
// Both are rendered server-side from the same suggestions, so switching is
// just a show/hide -- no extra request needed. The choice is remembered in
// localStorage so a page reload (e.g. after approving something from the
// list) reopens the view you were just in.
(function () {
  var toggle = document.getElementById('viewToggle');
  if (!toggle) return;

  var cardsView = document.getElementById('cardsView');
  var listView = document.getElementById('listView');
  var buttons = Array.prototype.slice.call(toggle.querySelectorAll('.view-toggle-btn'));
  var STORAGE_KEY = 'ag_dashboard_view';

  function setView(view) {
    var showList = view === 'list';
    listView.hidden = !showList;
    cardsView.hidden = showList;
    buttons.forEach(function (btn) {
      var active = btn.getAttribute('data-view') === view;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    try { window.localStorage.setItem(STORAGE_KEY, view); } catch (e) { /* private mode, etc. -- fine to skip */ }
  }

  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () { setView(btn.getAttribute('data-view')); });
  });

  var saved = null;
  try { saved = window.localStorage.getItem(STORAGE_KEY); } catch (e) { /* ignore */ }
  if (saved === 'list' || saved === 'cards') setView(saved);
})();
