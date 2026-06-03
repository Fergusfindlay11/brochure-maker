/**
 * Global Background Colour Picker -- applies chosen colour to the brochure theme and slide wrappers.
 */
(function () {
function initColourPicker() {
  if (window.__brochureColourPickerReady) return;
  window.__brochureColourPickerReady = true;

  var root = document.documentElement;
  var currentPrimaryColour = normalizeHex(
    getComputedStyle(root).getPropertyValue('--primary').trim()
  ) || '#B8714E';

  function normalizeHex(color) {
    var match = String(color || '').trim().match(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (!match) return '';

    var hex = match[1];
    if (hex.length === 3) {
      hex = hex.split('').map(function (ch) { return ch + ch; }).join('');
    }
    return '#' + hex.toUpperCase();
  }

  function shiftHex(hex, amount) {
    var normalized = normalizeHex(hex);
    if (!normalized) return hex;

    var target = amount < 0 ? 0 : 255;
    var ratio = Math.abs(amount);
    var value = parseInt(normalized.slice(1), 16);
    var channels = [
      (value >> 16) & 255,
      (value >> 8) & 255,
      value & 255,
    ];

    return '#' + channels.map(function (channel) {
      var shifted = Math.round(channel + (target - channel) * ratio);
      return shifted.toString(16).padStart(2, '0');
    }).join('').toUpperCase();
  }

  function applyThemeVariables(color) {
    var hex = normalizeHex(color);
    if (!hex) return color;

    var dark = shiftHex(hex, -0.16);
    var light = shiftHex(hex, 0.16);
    currentPrimaryColour = hex;

    root.style.setProperty('--primary', hex);
    root.style.setProperty('--primary-dark', dark);
    root.style.setProperty('--primary-light', light);
    root.style.setProperty('--terra', hex);
    root.style.setProperty('--terra-dark', dark);
    root.style.setProperty('--terra-light', light);

    return hex;
  }

  function applyColorToSlide(slide, color) {
    if (!slide || !slide.classList || !slide.classList.contains('slide-wrapper')) return;
    slide.style.background = normalizeHex(color) || color || currentPrimaryColour;
  }

  function applyColorToAll(color, options) {
    options = options || {};
    var appliedColor = applyThemeVariables(color);
    document.querySelectorAll('.slide-wrapper').forEach(function (s) {
      applyColorToSlide(s, appliedColor);
    });

    if (!options.silent) {
      document.dispatchEvent(new CustomEvent('brochure:colour-changed', {
        detail: { primaryColour: appliedColor },
      }));
    }

    if (!options.skipAutosave && window.requestBrochureAutoSave) {
      window.requestBrochureAutoSave(true);
    }

    return appliedColor;
  }

  function getBrochurePrimaryColour() {
    return normalizeHex(currentPrimaryColour) ||
      normalizeHex(getComputedStyle(root).getPropertyValue('--primary').trim()) ||
      '#B8714E';
  }

  document.addEventListener('brochure:layout-swapped', function (event) {
    var slide = event.detail && event.detail.slide;
    applyColorToSlide(slide, getBrochurePrimaryColour());
  });

  document.addEventListener('brochure:state-restored', function (event) {
    var state = event.detail && event.detail.state;
    var colour = normalizeHex(state && state.primaryColour) || getBrochurePrimaryColour();
    applyColorToAll(colour, { skipAutosave: true, silent: true });

    // Slide-manager may restore saved layout snapshots after this listener runs.
    // Reapply on the next task so replacement slide DOM gets the same palette.
    setTimeout(function () {
      applyColorToAll(colour, { skipAutosave: true, silent: true });
    }, 0);
  });

  // Swatch click handlers
  document.querySelectorAll('#globalSwatchRow .bg-swatch').forEach(function (swatch) {
    swatch.addEventListener('click', function (e) {
      e.stopPropagation();
      applyColorToAll(swatch.dataset.color);
      document.querySelectorAll('#globalSwatchRow .bg-swatch').forEach(function (s) { s.classList.remove('active'); });
      swatch.classList.add('active');
    });
  });

  // Custom colour input handler
  var globalCustomColor = document.getElementById('global-custom-color');
  if (globalCustomColor) {
    globalCustomColor.addEventListener('input', function (e) {
      applyColorToAll(e.target.value);
      document.querySelectorAll('#globalSwatchRow .bg-swatch').forEach(function (s) { s.classList.remove('active'); });
    });
    globalCustomColor.addEventListener('click', function (e) { e.stopPropagation(); });
  }

  // Expose for other modules if needed
  window.applyColorToAll = applyColorToAll;
  window.applyColourToAll = applyColorToAll;
  window.applyBrochureColourToSlide = applyColorToSlide;
  window.getBrochurePrimaryColour = getBrochurePrimaryColour;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initColourPicker);
} else {
  initColourPicker();
}
}());
