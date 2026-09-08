/* KPI 카운트업 애니메이션 공용 스크립트
 *
 * dashboard.html / supplier_report_detail.html에서 똑같은 애니메이션을 쓴다.
 * 예전엔 같은 코드가 두 템플릿에 복사돼 있어서, 한 곳만 고치면 나머지가 어긋났다.
 *
 * 사용법: .kpi-value 요소에 data-value(숫자) / data-suffix(선택) / data-decimals(선택)
 * 속성을 붙여두면, 페이지 로드 시 자동으로 0에서 목표값까지 애니메이션한다.
 * 별도 초기화 호출은 필요 없다(DOMContentLoaded에서 자동 실행).
 */
document.addEventListener('DOMContentLoaded', function() {
  var kpis = document.querySelectorAll('.kpi-value');
  kpis.forEach(function(el) {
    var target   = parseFloat(el.getAttribute('data-value')) || 0;
    var suffix   = el.getAttribute('data-suffix') || '';
    var decimals = parseInt(el.getAttribute('data-decimals') || '0', 10);
    var start    = performance.now();
    var duration = 900;

    function tick(now) {
      var elapsed = Math.min(now - start, duration);
      var t       = elapsed / duration;
      var ease    = 1 - Math.pow(1 - t, 3); // easeOutCubic
      var val     = target * ease;
      if (decimals > 0) {
        el.textContent = val.toFixed(decimals) + suffix;
      } else {
        var rounded = Math.round(val);
        el.textContent = (target > 999)
          ? rounded.toLocaleString() + suffix
          : rounded + suffix;
      }
      if (t < 1) requestAnimationFrame(tick);
      else {
        // 최종값 정확히 표시
        if (decimals > 0) el.textContent = target.toFixed(decimals) + suffix;
        else el.textContent = (target > 999) ? Math.round(target).toLocaleString() + suffix : Math.round(target) + suffix;
      }
    }
    requestAnimationFrame(tick);
  });
});
