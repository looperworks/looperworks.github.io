/* ===========================
   Threshold — Shared Scripts
   =========================== */

/* ----- Mobile Menu Toggle ----- */
(function() {
  var hamburger = document.getElementById('hamburger');
  var mobileMenu = document.getElementById('mobileMenu');
  if (hamburger && mobileMenu) {
    hamburger.addEventListener('click', function() {
      mobileMenu.classList.toggle('open');
    });
  }
})();

/* ----- Subscribe Handler — sends email to Google Sheets ----- */
function handleSubscribe(e) {
  e.preventDefault();
  var email = document.getElementById('subEmail').value;
  var form = document.getElementById('subscribeForm');
  var msg = document.getElementById('subscribeMsg');
  var btn = form.querySelector('button[type="submit"]');

  btn.textContent = 'Sending...';
  btn.disabled = true;

  fetch('https://script.google.com/macros/s/AKfycbws9nPFsEYYueyUbTIDEodASQOzvwa0Pmr7QfAW-nXkSTDgl4NtYjqGEJBLdEbAkcM/exec', {
    method: 'POST',
    mode: 'no-cors',
    redirect: 'follow',
    body: JSON.stringify({ email: email }),
    headers: { 'Content-Type': 'text/plain;charset=utf-8' }
  })
  .then(function() {
    form.style.display = 'none';
    msg.style.display = 'block';
  })
  .catch(function() {
    form.style.display = 'none';
    msg.style.display = 'block';
  });
}

/* ----- Cookie Consent Banner ----- */
(function() {
  var banner = document.getElementById('cookieBanner');
  var acceptBtn = document.getElementById('cookieAccept');
  if (!banner || !acceptBtn) return;
  if (localStorage.getItem('cookieConsent') === 'accepted') {
    banner.classList.add('hidden');
  }
  acceptBtn.addEventListener('click', function() {
    localStorage.setItem('cookieConsent', 'accepted');
    banner.classList.add('hidden');
  });
})();

/* ----- Animated Logo (auto-initializes if #logoCanvas exists) ----- */
(function() {
  var canvas = document.getElementById('logoCanvas');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');

  // Dimensions — compact for top-brand area
  var CW = 200, CH = 56;
  var dpr = window.devicePixelRatio || 1;
  canvas.width = CW * dpr;
  canvas.height = CH * dpr;
  canvas.style.width = CW + 'px';
  canvas.style.height = CH + 'px';
  ctx.scale(dpr, dpr);

  // Click navigates home — reads data-home attribute for path
  var homeLink = canvas.getAttribute('data-home') || '/';
  canvas.addEventListener('click', function() { window.location.href = homeLink; });

  // Logo mark parameters — scaled to fit CH
  var markW = 56, markH = 56;
  var markX = 0, markY = 0;
  var barW = 2.5, barGap = 2.5;
  var barCount = 11;
  var blkOffX = 10, blkOffY = 12;
  var blkW = 38, blkH = 43;
  var gapW = 1, gapSp = 5;

  // Text
  var text = 'Threshold';
  var fontFamily = "'Circular Std', -apple-system, BlinkMacSystemFont, sans-serif";
  var fontSize = 18;

  // Timing (ms)
  var LOGO_HOLD = 2500;
  var LOGO_FADE = 500;
  var TYPE_DELAY = 150;
  var CHAR_SPEED = 100;
  var TEXT_HOLD = 2500;
  var ERASE_DELAY = 150;
  var TEXT_ERASE = 70;
  var LOGO_FADE_IN = 500;
  var TYPE_DUR = text.length * CHAR_SPEED;
  var ERASE_DUR = text.length * TEXT_ERASE;
  var TOTAL = LOGO_HOLD + LOGO_FADE + TYPE_DELAY + TYPE_DUR + TEXT_HOLD + ERASE_DELAY + ERASE_DUR + LOGO_FADE_IN;

  function drawMark(a) {
    ctx.save();
    ctx.globalAlpha = a;
    ctx.fillStyle = '#111';
    for (var i = 0; i < barCount; i++) {
      ctx.fillRect(markX + i * (barW + barGap), markY, barW, markH);
    }
    // offset block
    var bx = markX + blkOffX, by = markY + blkOffY;
    var g = ctx.createLinearGradient(bx, 0, bx + blkW, 0);
    g.addColorStop(0, 'rgba(17,17,17,0.9)');
    g.addColorStop(0.55, 'rgba(17,17,17,0.9)');
    g.addColorStop(1, 'rgba(0,0,0,0.9)');
    ctx.fillStyle = g;
    ctx.fillRect(bx, by, blkW, blkH);
    ctx.fillStyle = 'rgba(255,255,255,0.95)';
    for (var j = 0; j < 8; j++) {
      var gx = bx + 2.5 + j * gapSp;
      if (gx + gapW <= bx + blkW) ctx.fillRect(gx, by, gapW, blkH);
    }
    ctx.restore();
  }

  function drawTyped(n) {
    ctx.save();
    ctx.fillStyle = '#111';
    ctx.font = '700 ' + fontSize + 'px ' + fontFamily;
    ctx.textBaseline = 'middle';
    var partial = text.substring(0, n);
    ctx.fillText(partial, 0, CH / 2);
    // cursor
    if (n < text.length) {
      var pw = ctx.measureText(partial).width;
      ctx.fillRect(pw + 2, CH / 2 - fontSize * 0.4, 2, fontSize * 0.7);
    }
    ctx.restore();
  }

  function ease(t) {
    return t < 0.5 ? 2*t*t : 1 - Math.pow(-2*t+2, 2)/2;
  }

  function frame(ts) {
    var t = ts % TOTAL;
    ctx.clearRect(0, 0, CW, CH);
    var el = t;

    if (el < LOGO_HOLD) { drawMark(1); }
    else if ((el -= LOGO_HOLD) < LOGO_FADE) { drawMark(1 - ease(el/LOGO_FADE)); }
    else if ((el -= LOGO_FADE) < TYPE_DELAY) { /* blank */ }
    else if ((el -= TYPE_DELAY) < TYPE_DUR) {
      drawTyped(Math.min(Math.floor(el/CHAR_SPEED)+1, text.length));
    }
    else if ((el -= TYPE_DUR) < TEXT_HOLD) { drawTyped(text.length); }
    else if ((el -= TEXT_HOLD) < ERASE_DELAY) { drawTyped(text.length); }
    else if ((el -= ERASE_DELAY) < ERASE_DUR) {
      var rem = Math.max(text.length - Math.floor(el/TEXT_ERASE), 0);
      if (rem > 0) drawTyped(rem);
    }
    else if ((el -= ERASE_DUR) < LOGO_FADE_IN) { drawMark(ease(el/LOGO_FADE_IN)); }

    requestAnimationFrame(frame);
  }

  document.fonts.ready.then(function() { requestAnimationFrame(frame); });
})();
