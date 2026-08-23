/* 서명 패드 공용 스크립트
 *
 * 승인 화면 / 부적합 통보서 / 업체 성적표에서 똑같은 서명 패드를 쓴다.
 * 예전엔 같은 코드가 세 템플릿에 복사돼 있어서, 한 곳만 고치면 나머지가 어긋났다.
 *
 * 사용법:
 *   var pad = SignaturePad.attach('sigPad');       // canvas id
 *   pad.isEmpty()        -> 아직 아무것도 안 그렸는지
 *   pad.toDataURL()      -> PNG dataURL
 *   pad.clear()          -> 지우기
 *   pad.loadDefault()    -> 로그인한 사람의 저장된 기본 서명 불러오기
 */
window.SignaturePad = (function () {
  function attach(canvasId) {
    var pad = document.getElementById(canvasId);
    if (!pad) return null;

    var ctx = pad.getContext('2d');
    var drawing = false;
    var hasInk = false;

    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = '#111';

    // 캔버스가 CSS로 축소돼 있을 수 있으므로 실제 좌표계로 환산해야 선이 어긋나지 않는다
    function pos(e) {
      var r = pad.getBoundingClientRect();
      var p = e.touches ? e.touches[0] : e;
      return {
        x: (p.clientX - r.left) * (pad.width / r.width),
        y: (p.clientY - r.top) * (pad.height / r.height)
      };
    }
    function start(e) { e.preventDefault(); drawing = true; var q = pos(e); ctx.beginPath(); ctx.moveTo(q.x, q.y); }
    function move(e) { if (!drawing) return; e.preventDefault(); var q = pos(e); ctx.lineTo(q.x, q.y); ctx.stroke(); hasInk = true; }
    function end() { drawing = false; }

    pad.addEventListener('mousedown', start);
    pad.addEventListener('mousemove', move);
    window.addEventListener('mouseup', end);
    pad.addEventListener('touchstart', start, { passive: false });
    pad.addEventListener('touchmove', move, { passive: false });
    pad.addEventListener('touchend', end);

    var api = {
      isEmpty: function () { return !hasInk; },
      toDataURL: function () { return pad.toDataURL('image/png'); },
      clear: function () { ctx.clearRect(0, 0, pad.width, pad.height); hasInk = false; },
      /* 저장해둔 기본 서명을 캔버스에 그려 넣는다. 없으면 false로 콜백 */
      loadDefault: function (done) {
        fetch('/signature/default/check')
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d.exists) { if (done) done(false); return; }
            var img = new Image();
            img.onload = function () {
              ctx.clearRect(0, 0, pad.width, pad.height);
              ctx.drawImage(img, 0, 0, pad.width, pad.height);
              hasInk = true;
              if (done) done(true);
            };
            img.src = d.url + '?t=' + Date.now();
          })
          .catch(function () { if (done) done(false); });
      },
      /* 폼 제출 직전 훅 — 서명이 없으면 막고, 있으면 hidden 필드에 담는다 */
      fillAndConfirm: function (hiddenInputId, confirmMessage) {
        if (api.isEmpty()) { alert('서명을 먼저 해줘.'); return false; }
        var el = document.getElementById(hiddenInputId);
        if (el) el.value = api.toDataURL();
        return confirmMessage ? confirm(confirmMessage) : true;
      }
    };
    return api;
  }

  return { attach: attach };
})();
