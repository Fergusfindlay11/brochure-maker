/**
 * Logo / Mark Picker — SVG logo marks, upload, size and position controls.
 */
document.addEventListener('DOMContentLoaded', function () {

  // SVG content for each logo option (keyed by data-logo value)
  var LOGO_MARKS = {
    grid: null, // null = restore original grid of spans
    diamond: '<svg viewBox="0 0 36 36" fill="none" style="width:100%;height:100%">' +
      '<polygon points="18,3 33,18 18,33 3,18" stroke="rgba(255,255,255,0.85)" stroke-width="1.8" fill="none"/>' +
      '<polygon points="18,10 26,18 18,26 10,18" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" fill="none"/>' +
      '<line x1="18" y1="3" x2="18" y2="33" stroke="rgba(255,255,255,0.35)" stroke-width="0.8"/>' +
      '<line x1="3" y1="18" x2="33" y2="18" stroke="rgba(255,255,255,0.35)" stroke-width="0.8"/>' +
      '</svg>',
    tower: '<svg viewBox="0 0 36 36" fill="none" style="width:100%;height:100%">' +
      '<rect x="10" y="8" width="16" height="24" stroke="rgba(255,255,255,0.85)" stroke-width="1.6" fill="none"/>' +
      '<polyline points="7,8 18,2 29,8" stroke="rgba(255,255,255,0.85)" stroke-width="1.6" stroke-linejoin="round" fill="none"/>' +
      '<rect x="14" y="22" width="8" height="10" stroke="rgba(255,255,255,0.85)" stroke-width="1.2" fill="none"/>' +
      '<rect x="12" y="14" width="5" height="5" stroke="rgba(255,255,255,0.7)" stroke-width="1.1" fill="none"/>' +
      '<rect x="19" y="14" width="5" height="5" stroke="rgba(255,255,255,0.7)" stroke-width="1.1" fill="none"/>' +
      '</svg>',
    'mono-b': '<svg viewBox="0 0 36 36" fill="none" style="width:100%;height:100%">' +
      '<text x="5" y="28" font-family="Georgia,serif" font-size="26" font-weight="700" fill="none" stroke="rgba(255,255,255,0.85)" stroke-width="1.2">B</text>' +
      '<line x1="5" y1="32" x2="31" y2="32" stroke="rgba(255,255,255,0.4)" stroke-width="1"/>' +
      '</svg>',
    arch: '<svg viewBox="0 0 36 36" fill="none" style="width:100%;height:100%">' +
      '<path d="M8 34 L8 16 Q8 4 18 4 Q28 4 28 16 L28 34" stroke="rgba(255,255,255,0.85)" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<line x1="8" y1="34" x2="28" y2="34" stroke="rgba(255,255,255,0.85)" stroke-width="1.8" stroke-linecap="round"/>' +
      '<path d="M13 34 L13 20 Q13 11 18 11 Q23 11 23 20 L23 34" stroke="rgba(255,255,255,0.5)" stroke-width="1.2" fill="none" stroke-linecap="round"/>' +
      '</svg>',
    hex: '<svg viewBox="0 0 36 36" fill="none" style="width:100%;height:100%">' +
      '<polygon points="18,3 31,10.5 31,25.5 18,33 5,25.5 5,10.5" stroke="rgba(255,255,255,0.85)" stroke-width="1.8" fill="none"/>' +
      '<polygon points="18,9 26,13.5 26,22.5 18,27 10,22.5 10,13.5" stroke="rgba(255,255,255,0.5)" stroke-width="1.1" fill="none"/>' +
      '</svg>',
    cross: '<svg viewBox="0 0 36 36" fill="none" style="width:100%;height:100%">' +
      '<rect x="4" y="14" width="28" height="8" stroke="rgba(255,255,255,0.85)" stroke-width="1.6" fill="none"/>' +
      '<rect x="14" y="4" width="8" height="28" stroke="rgba(255,255,255,0.85)" stroke-width="1.6" fill="none"/>' +
      '</svg>',
    circles: '<svg viewBox="0 0 36 36" fill="none" style="width:100%;height:100%">' +
      '<circle cx="18" cy="18" r="14" stroke="rgba(255,255,255,0.85)" stroke-width="1.6" fill="none"/>' +
      '<circle cx="18" cy="18" r="9" stroke="rgba(255,255,255,0.6)" stroke-width="1.2" fill="none"/>' +
      '<circle cx="18" cy="18" r="4" stroke="rgba(255,255,255,0.4)" stroke-width="1" fill="none"/>' +
      '</svg>',
    keymark: '<svg viewBox="0 0 36 36" fill="none" style="width:100%;height:100%">' +
      '<circle cx="13" cy="13" r="8" stroke="rgba(255,255,255,0.85)" stroke-width="1.6" fill="none"/>' +
      '<line x1="19" y1="19" x2="32" y2="32" stroke="rgba(255,255,255,0.85)" stroke-width="1.8" stroke-linecap="round"/>' +
      '<line x1="26" y1="26" x2="26" y2="30" stroke="rgba(255,255,255,0.7)" stroke-width="1.4" stroke-linecap="round"/>' +
      '<line x1="29.5" y1="29.5" x2="29.5" y2="33.5" stroke="rgba(255,255,255,0.7)" stroke-width="1.4" stroke-linecap="round"/>' +
      '</svg>',
  };

  // The current active logo (null = grid, string = svg html, or dataUrl for uploads)
  var currentLogoType = 'grid'; // 'grid' | 'svg:KEY' | 'upload'
  var uploadedLogoDataUrl = null;

  function buildGridContent(size) {
    if (size === 'large') {
      return Array(20).fill('<span></span>').join('');
    } else {
      return Array(20).fill('<span style="border-color:rgba(255,255,255,0.5)"></span>').join('');
    }
  }

  function applyLogoToAll(type, svgOrUrl) {
    document.querySelectorAll('.logo-zone').forEach(function (zone) {
      var size = zone.dataset.logoSize || 'small';
      zone.innerHTML = '';
      zone.style.display = '';

      if (type === 'grid') {
        // Restore original grid layout
        zone.style.display = 'grid';
        zone.innerHTML = buildGridContent(size);

      } else if (type === 'upload') {
        // Show uploaded image scaled to fit
        zone.style.display = 'flex';
        zone.style.alignItems = 'center';
        zone.style.justifyContent = 'center';
        var img = document.createElement('img');
        img.src = svgOrUrl;
        img.style.cssText = 'width:100%;height:100%;object-fit:contain;filter:brightness(0) invert(1);';
        zone.appendChild(img);

      } else {
        // SVG mark -- scale to zone size
        zone.style.display = 'flex';
        zone.style.alignItems = 'center';
        zone.style.justifyContent = 'center';
        zone.innerHTML = svgOrUrl;
      }
    });
  }

  // Wire up logo option clicks
  document.querySelectorAll('#logoPickRow .logo-pick-opt').forEach(function (opt) {
    opt.addEventListener('click', function (e) {
      e.stopPropagation();
      var key = opt.dataset.logo;
      document.querySelectorAll('#logoPickRow .logo-pick-opt, #logoPickRow .logo-upload-btn').forEach(function (o) { o.classList.remove('active'); });
      opt.classList.add('active');

      if (key === 'grid') {
        currentLogoType = 'grid';
        applyLogoToAll('grid');
      } else {
        currentLogoType = 'svg:' + key;
        applyLogoToAll('svg', LOGO_MARKS[key]);
      }
    });
  });

  // Upload button
  var logoUploadBtn = document.getElementById('logoUploadBtn');
  var logoFileInput = document.getElementById('logoFileInput');

  logoUploadBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    logoFileInput.click();
  });

  logoFileInput.addEventListener('change', function (e) {
    var file = e.target.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function (ev) {
      uploadedLogoDataUrl = ev.target.result;
      currentLogoType = 'upload';

      // Mark upload btn as active
      document.querySelectorAll('#logoPickRow .logo-pick-opt, #logoPickRow .logo-upload-btn').forEach(function (o) { o.classList.remove('active'); });
      logoUploadBtn.classList.add('active');

      // Show preview in upload button
      logoUploadBtn.innerHTML = '<img src="' + uploadedLogoDataUrl + '" style="width:36px;height:36px;object-fit:contain;filter:brightness(0) invert(1)">';

      applyLogoToAll('upload', uploadedLogoDataUrl);
    };
    reader.readAsDataURL(file);
  });

  logoUploadBtn.style.cssText += '; overflow:hidden;';

  // ============================================================
  // COVER LOGO SIZE & POSITION
  // ============================================================
  var coverLogoEl    = document.querySelector('#slide1 .cover-logo');
  var coverLogoZone  = document.querySelector('#slide1 .logo-zone');
  var sizeSlider     = document.getElementById('coverLogoSize');
  var sizeValLabel   = document.getElementById('coverLogoSizeVal');

  function applyCoverLogoSize(px) {
    if (!coverLogoZone) return;
    coverLogoZone.style.width  = px + 'px';
    coverLogoZone.style.height = Math.round(px * 1.24) + 'px'; // preserve original aspect ratio
    // Also update grid cell sizes for grid mode
    if (currentLogoType === 'grid') {
      var cell = Math.max(4, Math.round(px / 9));
      var gap  = Math.max(1, Math.round(cell * 0.22));
      coverLogoZone.style.gridTemplateColumns = 'repeat(4, ' + cell + 'px)';
      coverLogoZone.style.gridTemplateRows    = 'repeat(5, ' + cell + 'px)';
      coverLogoZone.style.gap = gap + 'px';
    }
  }

  sizeSlider.addEventListener('input', function () {
    var v = parseInt(sizeSlider.value);
    sizeValLabel.textContent = v + 'px';
    applyCoverLogoSize(v);
  });

  // Position buttons
  var coverSlide1 = document.getElementById('slide1');
  document.querySelectorAll('.pos-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      document.querySelectorAll('.pos-btn').forEach(function (b) {
        b.style.background = 'rgba(255,255,255,0.08)';
        b.style.color = 'rgba(255,255,255,0.7)';
        b.classList.remove('active');
      });
      btn.style.background = 'var(--terra)';
      btn.style.color = 'white';
      btn.classList.add('active');

      var pos = btn.dataset.pos;
      var logo = coverLogoEl;
      if (!logo) return;

      // Reset position styles
      logo.style.position = '';
      logo.style.top = '';
      logo.style.left = '';
      logo.style.right = '';
      logo.style.bottom = '';
      logo.style.alignSelf = '';
      logo.style.justifySelf = '';
      logo.style.marginBottom = '';
      coverSlide1.style.alignItems = '';

      if (pos === 'center') {
        logo.style.alignSelf = '';
        logo.style.marginBottom = '28px';
        coverSlide1.style.alignItems = 'center';
      } else if (pos === 'top-left') {
        logo.style.position = 'absolute';
        logo.style.top = '24px';
        logo.style.left = '28px';
      } else if (pos === 'top-right') {
        logo.style.position = 'absolute';
        logo.style.top = '24px';
        logo.style.right = '28px';
      } else if (pos === 'bottom-left') {
        logo.style.position = 'absolute';
        logo.style.bottom = '28px';
        logo.style.left = '28px';
      }
    });
  });

  // Expose for other modules if needed
  window.LOGO_MARKS = LOGO_MARKS;
  window.applyLogoToAll = applyLogoToAll;
  window.buildGridContent = buildGridContent;
});
