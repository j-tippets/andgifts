// Today: swipeable suggestion card stack.
// Progressively enhances the plain approve/skip <form> elements already
// rendered server-side — if this script fails to load or run, those forms
// remain visible and fully functional (normal POST + redirect).
//
// Approve is the only action that ever hits the server (permanently marks
// the SuggestedAction "approved"). Skip is purely a client-side "not right
// now": the card goes to the back of the stack instead of being removed, so
// nothing is lost. Once every remaining card has been skipped at least once
// in a row (a full lap with no approvals), a checkpoint card shows before
// the stack loops back to the top, so it's clear you've seen everything new
// and are about to start going back over skipped cards.
(function () {
  var stackWrap = document.getElementById('stackWrap');
  if (!stackWrap) return; // no suggestions today

  var stack = document.getElementById('stack');
  var cards = Array.prototype.slice.call(stack.querySelectorAll('.s-card'));
  var emptyState = document.getElementById('emptyState');
  var progressWrap = document.getElementById('progress');
  var progressLabel = document.getElementById('progressLabel');
  var btnApprove = document.getElementById('btnApprove');
  var btnSkip = document.getElementById('btnSkip');

  var originalTotal = cards.length;
  var approvedCount = 0;
  var skipsSinceLoop = 0;
  var busy = false; // true while a fetch (or a card/loop-card transition) is in flight

  // Working queue of not-yet-approved cards, front-to-back display order.
  // Skipping moves the front card to the back of this array; approving
  // removes it entirely.
  var queue = cards.slice();
  var showingLoop = false;

  // Built once, reused: the "you've seen everything new" checkpoint card.
  var loopCard = document.createElement('div');
  loopCard.className = 's-card s-card-loop accent-gold';
  loopCard.innerHTML =
    '<div class="accent"></div>' +
    '<div class="s-card-top"><h3>That\u2019s all your new tasks for now</h3></div>' +
    '<p class="s-loop-copy">Anything you skipped is still here whenever you want another look.</p>' +
    '<button type="button" class="btn btn-primary s-loop-btn">Review again</button>';
  stack.appendChild(loopCard);
  var loopBtn = loopCard.querySelector('.s-loop-btn');

  stackWrap.classList.add('js-active');

  for (var i = 0; i < originalTotal; i++) {
    progressWrap.appendChild(document.createElement('span'));
  }
  var segments = Array.prototype.slice.call(progressWrap.children);

  function updateProgress() {
    segments.forEach(function (s, i) { s.classList.toggle('done', i < approvedCount); });
    progressLabel.textContent = approvedCount + ' of ' + originalTotal + ' handled';
  }

  function currentDisplayList() {
    return showingLoop ? [loopCard].concat(queue) : queue;
  }

  function resetVisualState(card) {
    card.style.transition = 'none';
    card.style.transform = '';
    card.style.opacity = '';
    var approveStamp = card.querySelector('.stamp.approve');
    var skipStamp = card.querySelector('.stamp.skip');
    if (approveStamp) { approveStamp.style.transition = ''; approveStamp.style.opacity = ''; approveStamp.style.transform = ''; }
    if (skipStamp) { skipStamp.style.transition = ''; skipStamp.style.opacity = ''; skipStamp.style.transform = ''; }
  }

  function layout() {
    cards.forEach(function (card) { card.style.display = 'none'; });
    loopCard.style.display = 'none';

    var list = currentDisplayList();

    if (list.length === 0) {
      emptyState.classList.add('show');
      btnApprove.disabled = true;
      btnSkip.disabled = true;
      launchConfetti();
      updateProgress();
      return;
    }

    stackWrap.classList.toggle('js-loop', showingLoop);
    btnApprove.disabled = showingLoop;
    btnSkip.disabled = showingLoop;

    list.forEach(function (card, rel) {
      card.style.transition = 'transform .4s var(--ease), opacity .4s var(--ease)';
      if (rel > 3) { card.style.display = 'none'; return; }
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

  function submitApprove(card) {
    var form = card.querySelector('.s-form-approve');
    if (!form) return Promise.resolve(false);
    return fetch(form.getAttribute('action'), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: new FormData(form)
    }).then(function (res) { return res.ok; }).catch(function () { return false; });
  }

  function flyOut(card, direction, kind) {
    var flyX = direction === 'right' ? window.innerWidth : -window.innerWidth;
    var rot = direction === 'right' ? 18 : -18;
    card.style.transition = 'transform .45s var(--ease), opacity .45s var(--ease)';
    card.style.transform = 'translate(' + flyX + 'px, -10px) rotate(' + rot + 'deg)';
    card.style.opacity = 0;
    if (kind) {
      var stamp = card.querySelector('.stamp.' + kind);
      if (stamp) { stamp.style.transition = 'none'; stamp.style.opacity = 1; stamp.style.transform = 'scale(1) rotate(0deg)'; }
    }
  }

  function maybeQueueLoop() {
    if (!showingLoop && queue.length > 0 && skipsSinceLoop >= queue.length) {
      showingLoop = true;
    }
  }

  function completeTop(direction, viaDrag) {
    if (busy) return;
    var card = showingLoop ? loopCard : (queue.length ? queue[0] : null);
    if (!card) return;
    busy = true;
    var delay = viaDrag ? 260 : 380;

    if (showingLoop) {
      flyOut(card, direction, null);
      setTimeout(function () {
        showingLoop = false;
        skipsSinceLoop = 0;
        resetVisualState(loopCard);
        busy = false;
        layout();
      }, delay);
      return;
    }

    var kind = direction === 'right' ? 'approve' : 'skip';
    flyOut(card, direction, kind);

    if (kind === 'approve') {
      submitApprove(card).then(function (ok) {
        if (!ok) {
          var form = card.querySelector('.s-form-approve');
          if (form) { form.submit(); return; }
        }
        setTimeout(function () {
          queue.shift();
          approvedCount++;
          maybeQueueLoop();
          busy = false;
          layout();
        }, delay);
      });
    } else {
      // Skip: nothing is sent to the server. The card just moves to the
      // back of the queue so it comes back around later.
      setTimeout(function () {
        var skipped = queue.shift();
        queue.push(skipped);
        skipsSinceLoop++;
        resetVisualState(skipped);
        maybeQueueLoop();
        busy = false;
        layout();
      }, delay);
    }
  }

  btnApprove.addEventListener('click', function () { completeTop('right', false); });
  btnSkip.addEventListener('click', function () { completeTop('left', false); });
  loopBtn.addEventListener('click', function () { completeTop('right', false); });

  // --- drag / swipe on top card ---
  var dragging = false, startX = 0, startY = 0, dx = 0, activeCard = null;

  function topEl() {
    return showingLoop ? loopCard : (queue.length ? queue[0] : null);
  }

  function pointerDown(e) {
    if (busy) return;
    var card = topEl();
    if (!card) return;
    if (e.target.closest('.s-card') !== card) return;
    if (e.target.closest('.s-form') || e.target.closest('.s-loop-btn') || e.target.closest('.s-edit-disclosure') || e.target.closest('.s-gift-box')) return; // let real buttons work untouched
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
    if (approveStamp) {
      approveStamp.style.transition = 'none';
      approveStamp.style.opacity = Math.max(0, Math.min(1, dx / 90));
      approveStamp.style.transform = 'scale(' + (.6 + Math.min(1, dx / 90) * .4) + ') rotate(0deg)';
    }
    if (skipStamp) {
      skipStamp.style.transition = 'none';
      skipStamp.style.opacity = Math.max(0, Math.min(1, -dx / 90));
      skipStamp.style.transform = 'scale(' + (.6 + Math.min(1, -dx / 90) * .4) + ') rotate(0deg)';
    }
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
      if (approveStamp) { approveStamp.style.transition = 'opacity .3s var(--ease)'; approveStamp.style.opacity = 0; }
      if (skipStamp) { skipStamp.style.transition = 'opacity .3s var(--ease)'; skipStamp.style.opacity = 0; }
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
