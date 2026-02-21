/**
 * Global Background Colour Picker -- applies chosen colour to ALL slide wrappers.
 */
document.addEventListener('DOMContentLoaded', function () {
  var allSlides = document.querySelectorAll('.slide-wrapper');

  function applyColorToAll(color) {
    allSlides.forEach(function (s) { s.style.background = color; });
  }

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
  globalCustomColor.addEventListener('input', function (e) {
    applyColorToAll(e.target.value);
    document.querySelectorAll('#globalSwatchRow .bg-swatch').forEach(function (s) { s.classList.remove('active'); });
  });
  globalCustomColor.addEventListener('click', function (e) { e.stopPropagation(); });

  // Expose for other modules if needed
  window.applyColorToAll = applyColorToAll;
});
