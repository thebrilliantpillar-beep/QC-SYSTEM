/* 접기/펼치기 구역(<details>) 공용 스크립트
 *
 * 품질 대시보드 / 불량 이력 등 여러 화면에서 <details data-sec="..."> 기반
 * 접기/펼치기 구역을 쓴다. 열림/닫힘 상태를 localStorage에 기억하는 로직이
 * 화면마다 복사돼 있었다 — 한 곳만 고치면 나머지가 어긋나는 문제가 있어서
 * 서명 패드(static/signature_pad.js)처럼 공용 스크립트 하나로 뽑았다.
 *
 * 사용법:
 *   CollapsibleSections.init('details.sec', 'dash-sec-');
 *   // selector : <details data-sec="..."> 요소들을 고르는 CSS 선택자
 *   // keyPrefix: localStorage 키 접두어 (뒤에 data-sec 값이 그대로 붙는다)
 */
window.CollapsibleSections = (function () {
  function init(selector, keyPrefix) {
    document.querySelectorAll(selector).forEach(function (d) {
      var key = keyPrefix + d.dataset.sec;
      try {
        var saved = localStorage.getItem(key);
        if (saved !== null) d.open = (saved === '1');
      } catch (e) {}
      d.addEventListener('toggle', function () {
        try { localStorage.setItem(key, d.open ? '1' : '0'); } catch (e) {}
      });
    });
  }

  return { init: init };
})();
