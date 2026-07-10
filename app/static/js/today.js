// Today: swipeable suggestion card stack.
// Progressively enhances the plain approve/skip <form> elements already
// rendered server-side — if this script fails to load or run, those forms
// remain visible and fully functional (normal POST + redirect).
(function () {
  var stackWrap = document.getElementById('stackWrap');
  if (!stackWrap) return; // no suggestions today

  var stack = document.getElementById('stack');
  var cards = Array.prototype.slice.call(stack.querySelectorAll('.s-card'));
  var emptyState = document.getElementById('emptyState');
  var progressWrap = document.getElementById('progress');
  var progressLabel = document.getElementById('progressLabel');
  var swipeHint = document.getElementById('swipeHint');
  var btnApprove = document.getElementById('btnApprove');
  var btnSkip = document.getElementById('btnSkip');

  var total = cards.length;
  var index = 0;
  var busy = false; // true while a fetch is in flight, to avoid double-submits

  stackWrap.classList.add('js-active');

  for (var i = 0; i < total; i++) {
    progressWrap.appendChild(document.createElement('span'));
  }
  var segments = Array.prototype.slice.call(progressWrap.children);

  function updateProgress() {
    segments.forEach(function (s, i) { s.classList.toggle('done', i < index); });
    progressLabel.textContent = index < total ? (index + 1) + ' of ' + total : total + ' of ' + total;
  }

  function layout() {
    cards.forEach(function (card, i) {
      var rel = i - index;
      card.style.transition = 'transform .4s var(--ease), opacity .4s var(--ease)';
      if (rel < 0) { card.style.display = 'none'; return; }
      card.style.display = 'flex';
      if (rel === 0) {
        card.style.zIndex = 10;
        card.style.transform = 'translateY(0) scale(1) rotate(0deg)';
        card.style.opacity = 1;
        card.style.cursor = 'grab';
      } else if (rel === 1) {
        card.style.zIndex = 9;
        card.style.transform = 'translateY(14px) scale(.96)';
        card.style.opacity = .85;
      } else if (rel === 2) {
        card.style.zIndex = 8;
        card.style.transform = 'translateY(26px) scale(.92)';
        card.style.opacity = .55;
      } else {
        card.style.zIndex = 1;
        card.style.transform = 'translateY(34px) scale(.9)';
        card.style.opacity = 0;
      }
    });
    if (index >= total) {
      emptyState.classList.add('show');
      btnApprove.disabled = true;
      btnSkip.disabled = true;
      launchConfetti();
    }
    updateProgress();
  }

  function launchConfetti() {
    var colors = ['var(--mint)', 'var(--coral)', 'var(--gold)'];
    for (var i = 0; i < 16; i++) {
      var dot = document.createElement('div');
      dot.className = 'confetti-dot';
      var size = 5 + Math.random() * 5;
      dot.style.width = size + 'px';
      dot.style.height = size + 'px';
      dot.style.background = colors[i % colors.length];
      dot.style.left = (10 + Math.random() * 80) + '%';
      dot.style.top = '30%';
      emptyState.appendChild(dot);
      var fall = 60 + Math.random() * 60;
      var drift = (Math.random() - .5) * 60;
      dot.animate([
        { transform: 'translate(0,0) rotate(0deg)', opacity: 0 },
        { transform: 'translate(0,10px) rotate(90deg)', opacity: 1, offset: .15 },
        { transform: 'translate(' + drift + 'px,' + fall + 'px) rotate(280deg)', opacity: 0 }
      ], { duration: 1400 + Math.random() * 600, delay: i * 40, easing: 'cubic-bezier(.22,1,.36,1)' });
    }
  }

  function submitAction(card, kind) {
    var form = card.querySelector(kind === 'approve' ? '.s-form-approve' : '.s-form-skip');
    if (!form) return Promise.resolve(false);
    return fetch(form.getAttribute('action'), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    }).then(function (res) { return res.ok; }).catch(function () { return false; });
  }

  function completeTop(direction, viaDrag) {
    if (index >= total || busy) return;
    busy = true;
    var card = cards[index];
    var kind = direction === 'right' ? 'approve' : 'skip';
    var flyX = direction === 'right' ? window.innerWidth : -window.innerWidth;
    var rot = direction === 'right' ? 18 : -18;

    card.style.transition = 'transform .45s var(--ease), opacity .45s var(--ease)';
    card.style.transform = 'translate(' + flyX + 'px, -10px) rotate(' + rot + 'deg)';
    card.style.opacity = 0;
    var stamp = card.querySelector('.stamp.' + kind);
    if (stamp) { stamp.style.transition = 'none'; stamp.style.opacity = 1; stamp.style.transform = 'scale(1) rotate(0deg)'; }

    submitAction(card, kind).then(function (ok) {
      if (!ok) {
        // Fall back to a real form submit so the action still lands even if
        // the fetch failed (offline, server error, etc.)
        var form = card.querySelector(kind === 'approve' ? '.s-form-approve' : '.s-form-skip');
        if (form) { form.submit(); return; }
      }
      setTimeout(function () {
        index++;
        busy = false;
        layout();
      }, viaDrag ? 260 : 380);
    });
  }

  btnApprove.addEventListener('click', function () { completeTop('right', false); });
  btnSkip.addEventListener('click', function () { completeTop('left', false); });

  // --- drag / swipe on top card ---
  var dragging = false, startX = 0, startY = 0, dx = 0, activeCard = null;

  function pointerDown(e) {
    if (index >= total || busy) return;
    var card = cards[index];
    if (e.target.closest('.s-card') !== card) return;
    if (e.target.closest('.s-form')) return; // let fallback form buttons work untouched
    dragging = true;
    activeCard = card;
    startX = e.touches ? e.touches[0].clientX : e.clientX;
    startY = e.touches ? e.touches[0].clientY : e.clientY;
    card.style.transition = 'none';
    card.style.cursor = 'grabbing';
  }
  function pointerMove(e) {
    if (!dragging || !activeCard) return;
    var x = e.touches ? e.touches[0].clientX : e.clientX;
    var y = e.touches ? e.touches[0].clientY : e.clientY;
    dx = x - startX;
    var dyRaw = y - startY;
    if (Math.abs(dx) < 6 && Math.abs(dyRaw) < 6) return;
    var rot = dx / 18;
    activeCard.style.transform = 'translate(' + dx + 'px,' + (dyRaw * .15) + 'px) rotate(' + rot + 'deg)';
    var approveStamp = activeCard.querySelector('.stamp.approve');
    var skipStamp = activeCard.querySelector('.stamp.skip');
    approveStamp.style.transition = 'none';
    skipStamp.style.transition = 'none';
    approveStamp.style.opacity = Math.max(0, Math.min(1, dx / 90));
    approveStamp.style.transform = 'scale(' + (.6 + Math.min(1, dx / 90) * .4) + ') rotate(0deg)';
    skipStamp.style.opacity = Math.max(0, Math.min(1, -dx / 90));
    skipStamp.style.transform = 'scale(' + (.6 + Math.min(1, -dx / 90) * .4) + ') rotate(0deg)';
  }
  function pointerUp() {
    if (!dragging || !activeCard) return;
    dragging = false;
    var threshold = 100;
    if (dx > threshold) {
      completeTop('right', true);
    } else if (dx < -threshold) {
      completeTop('left', true);
    } else {
      activeCard.style.transition = 'transform .35s var(--ease)';
      activeCard.style.transform = 'translate(0,0) rotate(0deg)';
      activeCard.style.cursor = 'grab';
      var approveStamp = activeCard.querySelector('.stamp.approve');
      var skipStamp = activeCard.querySelector('.stamp.skip');
      approveStamp.style.transition = 'opacity .3s var(--ease)';
      skipStamp.style.transition = 'opacity .3s var(--ease)';
      approveStamp.style.opacity = 0;
      skipStamp.style.opacity = 0;
    }
    activeCard = null;
    dx = 0;
  }

  stack.addEventListener('mousedown', pointerDown);
  window.addEventListener('mousemove', pointerMove);
  window.addEventListener('mouseup', pointerUp);
  stack.addEventListener('touchstart', pointerDown, { passive: true });
  window.addEventListener('touchmove', pointerMove, { passive: true });
  window.addEventListener('touchend', pointerUp);

  layout();
})();
