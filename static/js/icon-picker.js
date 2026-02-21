/**
 * Icon Picker — overlay for choosing icons for .highlight-icon and .managed-icon elements.
 * Depends on: window.SVG_ICONS, window.ICON_LOOKUP (from svg-library.js)
 */
document.addEventListener('DOMContentLoaded', function () {
  let activeIconEl = null;
  const overlay  = document.getElementById('iconPickerOverlay');
  const panel    = document.getElementById('iconPickerPanel');
  const iconGrid = document.getElementById('iconGrid');
  const catsBar  = document.getElementById('iconPickerCats');

  function renderCatTabs(activecat) {
    catsBar.innerHTML = Object.keys(window.SVG_ICONS).map(function (cat) {
      return '<button class="cat-tab' + (cat === activecat ? ' active' : '') + '" data-cat="' + cat + '"' +
        ' style="background:' + (cat === activecat ? 'var(--terra)' : 'rgba(255,255,255,0.1)') + ';border:none;color:rgba(255,255,255,0.85);' +
        'font-size:9px;letter-spacing:0.1em;padding:4px 9px;border-radius:3px;cursor:pointer;font-family:Jost,sans-serif;">' + cat + '</button>';
    }).join('');
    catsBar.querySelectorAll('.cat-tab').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        renderIconGrid(btn.dataset.cat);
        renderCatTabs(btn.dataset.cat);
      });
    });
  }

  function renderIconGrid(cat) {
    var icons = window.SVG_ICONS[cat];
    iconGrid.innerHTML = Object.entries(icons).map(function (entry) {
      var id = entry[0], svg = entry[1];
      return '<div class="icon-opt" data-icon-id="' + id + '" title="' + id + '">' + svg + '</div>';
    }).join('');
    iconGrid.querySelectorAll('.icon-opt').forEach(function (opt) {
      opt.addEventListener('click', function (e) {
        e.stopPropagation();
        if (activeIconEl) {
          activeIconEl.innerHTML = window.ICON_LOOKUP[opt.dataset.iconId] || opt.innerHTML;
          activeIconEl.dataset.iconId = opt.dataset.iconId;
        }
        closeIconPicker();
      });
    });
  }

  function openIconPicker(el, e) {
    e.stopPropagation();
    activeIconEl = el;
    overlay.classList.add('open');
    var firstCat = Object.keys(window.SVG_ICONS)[0];
    renderCatTabs(firstCat);
    renderIconGrid(firstCat);
    var rect = el.getBoundingClientRect();
    var top = rect.bottom + 10, left = rect.left - 50;
    if (left < 8) left = 8;
    if (left + 310 > window.innerWidth) left = window.innerWidth - 318;
    if (top + 380 > window.innerHeight) top = rect.top - 390;
    panel.style.top = top + 'px';
    panel.style.left = left + 'px';
  }

  function closeIconPicker() {
    overlay.classList.remove('open');
    activeIconEl = null;
  }

  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) closeIconPicker();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeIconPicker();
  });

  // Attach to highlight icons
  document.querySelectorAll('.highlight-icon').forEach(function (el) {
    el.addEventListener('click', function (e) { openIconPicker(el, e); });
  });

  // Attach to managed icons
  document.querySelectorAll('.managed-icon').forEach(function (el) {
    el.addEventListener('click', function (e) { openIconPicker(el, e); });
  });

  panel.style.width = '300px';

  // Expose for other modules that may need to close the picker
  window.closeIconPicker = closeIconPicker;
});
