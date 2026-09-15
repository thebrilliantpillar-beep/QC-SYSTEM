/* 사진 리사이즈 공용 스크립트 (2026-09-15, 출고 스캔 체감속도 개선)
 *
 * 촬영/선택한 사진을 서버로 보내기 전에 브라우저에서 미리 축소해서 업로드
 * 데이터량 자체를 줄인다(느린 모바일 네트워크에서 체감 시간을 줄이는 핵심
 * 레버 — 서버 압축 설정 변경만으로는 처리시간이 거의 안 줄었다, app.py
 * _save_ncr_photo 참고). 서버 목표(1280px)와 같은 값으로 미리 줄여두면
 * 서버가 다시 리사이즈할 일이 없고(업스케일도 안 함), 무엇보다 업로드
 * 바이트 수 자체가 줄어든다.
 *
 * EXIF Orientation 관련 중요 사실(2026-09-15, 실측 확인 — 다시 만들지 말 것):
 * 처음엔 JPEG APP1(EXIF) 마커를 직접 파싱해서 캔버스에 회전 행렬을 수동으로
 * 적용하는 방식으로 만들었으나, 실제로 Orientation=6/3/8 태그를 심은 테스트
 * 이미지로 헤드리스 크롬에서 픽셀 단위로 검증한 결과 **이중 회전 버그**였다.
 * 크롬을 포함한 최신 브라우저는 `<img>` 로 이미지를 로드하는 시점에 이미
 * EXIF Orientation을 자동 보정한다(naturalWidth/Height 자체가 보정된 값으로
 * 나옴 — orientation=6인 300x200 원본이 img.naturalWidth/Height=200x300으로
 * 나온 것으로 실측 확인). 즉 `ctx.drawImage(img, ...)`가 그리는 픽셀은 이미
 * 올바른 방향이라, 여기에 수동 파싱 결과로 또 회전을 적용하면 방향이
 * 틀어진다. 그래서 이 파일은 EXIF를 직접 읽지 않는다 — `<img>`가 그려주는
 * 대로 캔버스 크기만 맞춰 리사이즈하면 방향이 항상 맞다(이 앱의 대상
 * 브라우저는 태블릿/PC의 최신 Chrome 계열이라 이 동작을 신뢰해도 됨).
 *
 * 사용법:
 *   PhotoResize.resizeFile(file).then(function (resizedFile) { ... });
 * 실패(이미지 디코딩 실패 등)하면 항상 원본 file을 그대로 resolve한다 —
 * 사진 촬영/업로드 자체가 막히면 안 되기 때문(app.py의 _save_ncr_photo와
 * 같은 폴백 철학).
 */
window.PhotoResize = (function () {
  var TARGET_MAX = 1280;   // 서버 목표(1280px)와 동일하게 맞춤
  var QUALITY = 0.85;      // 서버가 최종적으로 80%로 다시 인코딩하므로
                            // 클라이언트 품질은 여유 있게(이중 압축 열화 최소화)

  function resizeFile(file) {
    if (!file || !/^image\//.test(file.type || '')) return Promise.resolve(file);
    return new Promise(function (resolve) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        URL.revokeObjectURL(url);
        try {
          var w = img.naturalWidth, h = img.naturalHeight;
          var scale = Math.min(1, TARGET_MAX / Math.max(w, h));
          var dw = Math.round(w * scale), dh = Math.round(h * scale);
          var canvas = document.createElement('canvas');
          canvas.width = dw;
          canvas.height = dh;
          var ctx = canvas.getContext('2d');
          ctx.drawImage(img, 0, 0, dw, dh);
          canvas.toBlob(function (blob) {
            if (!blob) { resolve(file); return; }
            var name = (file.name || 'photo').replace(/\.\w+$/, '') + '.jpg';
            resolve(new File([blob], name, { type: 'image/jpeg' }));
          }, 'image/jpeg', QUALITY);
        } catch (err) {
          resolve(file);
        }
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  return { resizeFile: resizeFile };
})();
