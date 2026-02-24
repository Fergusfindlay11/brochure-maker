/**
 * Image Crop & Reposition
 * Double-click an image placeholder with a photo to enter crop mode.
 * Drag to pan background-position, mouse wheel to zoom background-size.
 */
document.addEventListener('DOMContentLoaded', function () {
  var activeEl = null;
  var startX = 0, startY = 0;
  var startPosX = 50, startPosY = 50;
  var controls = null;

  // Listen for double-click on image placeholders
  document.addEventListener('dblclick', function (e) {
    var placeholder = e.target.closest('.img-placeholder');
    if (!placeholder) return;

    // Only activate if the placeholder has an image
    var bg = placeholder.style.backgroundImage;
    if (!bg || bg === 'none') return;

    e.preventDefault();
    e.stopPropagation();
    enterCropMode(placeholder);
  });

  function enterCropMode(el) {
    if (activeEl) exitCropMode();
    activeEl = el;
    el.classList.add('img-crop-active');

    // Disable contenteditable while cropping
    el.setAttribute('data-was-editable', el.getAttribute('contenteditable') || '');
    el.removeAttribute('contenteditable');

    // Read current position/zoom from data attributes or defaults
    var posX = parseFloat(el.getAttribute('data-bg-x')) || 50;
    var posY = parseFloat(el.getAttribute('data-bg-y')) || 50;
    var zoom = parseFloat(el.getAttribute('data-bg-zoom')) || 100;

    el.style.backgroundPosition = posX + '% ' + posY + '%';
    el.style.backgroundSize = zoom + '%';

    // Create controls
    controls = document.createElement('div');
    controls.className = 'img-crop-controls';
    controls.innerHTML =
      '<label>Zoom</label>' +
      '<input type="range" min="100" max="300" value="' + zoom + '" id="cropZoom">' +
      '<button id="cropReset">Reset</button>' +
      '<button id="cropDone">Done</button>';
    el.style.position = 'relative';
    el.appendChild(controls);

    var zoomSlider = controls.querySelector('#cropZoom');
    zoomSlider.addEventListener('input', function () {
      var z = parseFloat(this.value);
      activeEl.style.backgroundSize = z + '%';
      activeEl.setAttribute('data-bg-zoom', z);
    });

    controls.querySelector('#cropReset').addEventListener('click', function (e) {
      e.stopPropagation();
      activeEl.style.backgroundPosition = '50% 50%';
      activeEl.style.backgroundSize = '100%';
      activeEl.setAttribute('data-bg-x', 50);
      activeEl.setAttribute('data-bg-y', 50);
      activeEl.setAttribute('data-bg-zoom', 100);
      zoomSlider.value = 100;
    });

    controls.querySelector('#cropDone').addEventListener('click', function (e) {
      e.stopPropagation();
      exitCropMode();
    });

    // Drag to pan
    el.addEventListener('mousedown', onDragStart);
    el.addEventListener('wheel', onWheel, { passive: false });
  }

  function onDragStart(e) {
    if (!activeEl || e.target.closest('.img-crop-controls')) return;
    e.preventDefault();
    startX = e.clientX;
    startY = e.clientY;
    startPosX = parseFloat(activeEl.getAttribute('data-bg-x')) || 50;
    startPosY = parseFloat(activeEl.getAttribute('data-bg-y')) || 50;
    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('mouseup', onDragEnd);
  }

  function onDragMove(e) {
    if (!activeEl) return;
    var dx = e.clientX - startX;
    var dy = e.clientY - startY;
    // Convert pixel delta to percentage (inverted for natural feel)
    var rect = activeEl.getBoundingClientRect();
    var newX = Math.max(0, Math.min(100, startPosX - (dx / rect.width) * 100));
    var newY = Math.max(0, Math.min(100, startPosY - (dy / rect.height) * 100));
    activeEl.style.backgroundPosition = newX + '% ' + newY + '%';
    activeEl.setAttribute('data-bg-x', Math.round(newX * 10) / 10);
    activeEl.setAttribute('data-bg-y', Math.round(newY * 10) / 10);
  }

  function onDragEnd() {
    document.removeEventListener('mousemove', onDragMove);
    document.removeEventListener('mouseup', onDragEnd);
  }

  function onWheel(e) {
    if (!activeEl) return;
    e.preventDefault();
    var zoom = parseFloat(activeEl.getAttribute('data-bg-zoom')) || 100;
    zoom += e.deltaY < 0 ? 10 : -10;
    zoom = Math.max(100, Math.min(300, zoom));
    activeEl.style.backgroundSize = zoom + '%';
    activeEl.setAttribute('data-bg-zoom', zoom);
    var slider = controls && controls.querySelector('#cropZoom');
    if (slider) slider.value = zoom;
  }

  function exitCropMode() {
    if (!activeEl) return;
    activeEl.classList.remove('img-crop-active');
    // Restore contenteditable
    var was = activeEl.getAttribute('data-was-editable');
    if (was) activeEl.setAttribute('contenteditable', was);
    activeEl.removeAttribute('data-was-editable');
    // Remove controls
    if (controls && controls.parentNode) controls.remove();
    controls = null;
    // Remove listeners
    activeEl.removeEventListener('mousedown', onDragStart);
    activeEl.removeEventListener('wheel', onWheel);
    activeEl = null;
    // Push undo state if available
    if (window.pushUndoState) window.pushUndoState();
  }

  // Escape to exit
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && activeEl) exitCropMode();
  });

  // Click outside to exit
  document.addEventListener('mousedown', function (e) {
    if (!activeEl) return;
    if (!activeEl.contains(e.target)) exitCropMode();
  });
});
