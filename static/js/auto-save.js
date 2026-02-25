/**
 * Auto-Save & State Persistence
 * Saves editor state (all contenteditable text, images, map SVGs, styles)
 * every 30s. Restores state on page load if available.
 *
 * All elements are matched by a stable `data-save-id` attribute rather than
 * array index, so adding/removing DOM nodes (e.g. map generation removing
 * .img-placeholder) cannot shift other elements' restore targets.
 */
document.addEventListener('DOMContentLoaded', function () {
  var SAVE_INTERVAL = 30000; // 30 seconds
  var DEBOUNCE_MS = 5000;    // 5 seconds after last edit
  var projectId = window.__PROJECT_ID__;
  if (!projectId) return;

  var saveTimer = null;
  var debounceTimer = null;
  var lastSavedHash = '';
  var indicator = null;

  // ── Assign stable data-save-id to every persistable element ──
  (function assignSaveIds() {
    document.querySelectorAll('.slide-wrapper').forEach(function (slide) {
      var slideId = slide.id || '';

      // contenteditable elements
      slide.querySelectorAll('[contenteditable="true"]').forEach(function (el, i) {
        if (!el.getAttribute('data-save-id')) {
          el.setAttribute('data-save-id', slideId + '__ce__' + i);
        }
      });

      // img-placeholder elements
      slide.querySelectorAll('.img-placeholder').forEach(function (el, i) {
        if (!el.getAttribute('data-save-id')) {
          el.setAttribute('data-save-id', slideId + '__ph__' + i);
        }
      });

      // map-area (even after it loses .img-placeholder)
      slide.querySelectorAll('.map-area').forEach(function (el, i) {
        if (!el.getAttribute('data-save-id')) {
          el.setAttribute('data-save-id', slideId + '__map__' + i);
        }
      });
    });
  })();

  // Create save indicator in nav bar
  var nav = document.querySelector('.slide-nav');
  if (nav) {
    indicator = document.createElement('span');
    indicator.className = 'autosave-indicator';
    indicator.textContent = '';
    nav.appendChild(indicator);
  }

  // Try to restore saved state on load
  restoreState();

  // Set up auto-save interval
  saveTimer = setInterval(function () {
    saveState();
  }, SAVE_INTERVAL);

  // Also save on changes (debounced)
  var slidesContainer = document.querySelector('.slide-wrapper');
  if (slidesContainer) slidesContainer = slidesContainer.parentNode;
  if (slidesContainer) {
    var observer = new MutationObserver(function () {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(saveState, DEBOUNCE_MS);
      showIndicator('editing');
    });
    observer.observe(slidesContainer, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['style', 'data-bg-x', 'data-bg-y', 'data-bg-zoom'],
    });
  }

  // Save before page unload
  window.addEventListener('beforeunload', function () {
    saveStateSync();
  });

  function serializeState() {
    var state = {};

    // ── Editable texts — keyed by data-save-id ──
    var editableTexts = {};
    document.querySelectorAll('[contenteditable="true"]').forEach(function (el) {
      var saveId = el.getAttribute('data-save-id');
      if (!saveId) return; // skip elements without an ID
      editableTexts[saveId] = { html: el.innerHTML };
    });
    state.editableTexts = editableTexts;

    // ── Background images — keyed by data-save-id ──
    // Capture from .img-placeholder elements AND any element with a
    // data-save-id that contains '__ph__' (in case class was removed)
    var images = {};
    document.querySelectorAll('.img-placeholder, [data-save-id*="__ph__"]').forEach(function (el) {
      var saveId = el.getAttribute('data-save-id');
      if (!saveId) return;
      images[saveId] = {
        bgImage: el.style.backgroundImage || '',
        bgPosition: el.style.backgroundPosition || '',
        bgSize: el.style.backgroundSize || '',
        bgX: el.getAttribute('data-bg-x') || '',
        bgY: el.getAttribute('data-bg-y') || '',
        bgZoom: el.getAttribute('data-bg-zoom') || '',
      };
    });
    state.images = images;

    // ── Map SVGs — keyed by data-save-id of .map-area ──
    var mapSvg = {};
    document.querySelectorAll('.map-area').forEach(function (el) {
      var saveId = el.getAttribute('data-save-id');
      if (!saveId) return;
      if (el.classList.contains('map-loaded')) {
        var svg = el.querySelector('svg');
        if (svg) {
          mapSvg[saveId] = { svgContent: svg.outerHTML };
        }
      }
    });
    state.mapSvg = mapSvg;

    // Capture colour scheme
    var root = document.documentElement;
    state.primaryColour = getComputedStyle(root).getPropertyValue('--primary').trim();

    // ── V1 Map Pipeline: persist style tokens and hash ──
    if (window.__mapStyleTokens) {
      state.mapStyleTokens = window.__mapStyleTokens;
      state.mapStyleHash = window.__mapStyleHash || '';
    }

    return state;
  }

  function saveState() {
    var state = serializeState();
    var hash = simpleHash(JSON.stringify(state));
    if (hash === lastSavedHash) return; // No changes

    showIndicator('saving');

    fetch('/api/projects/' + projectId + '/state', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state),
    })
      .then(function (res) {
        if (res.ok) {
          lastSavedHash = hash;
          showIndicator('saved');
        } else {
          showIndicator('error');
        }
      })
      .catch(function () {
        showIndicator('error');
      });
  }

  function saveStateSync() {
    var state = serializeState();
    var hash = simpleHash(JSON.stringify(state));
    if (hash === lastSavedHash) return;

    // Use sendBeacon for reliable beforeunload saving
    var blob = new Blob([JSON.stringify(state)], { type: 'application/json' });
    navigator.sendBeacon('/api/projects/' + projectId + '/state', blob);
  }

  function restoreState() {
    fetch('/api/projects/' + projectId + '/state')
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (state) {
        if (!state) return;
        applyState(state);
        showIndicator('saved');
      })
      .catch(function () {
        // No saved state, that's fine
      });
  }

  function applyState(state) {
    // ── Restore editable text — match by data-save-id ──
    if (state.editableTexts && typeof state.editableTexts === 'object' && !Array.isArray(state.editableTexts)) {
      Object.keys(state.editableTexts).forEach(function (saveId) {
        var el = document.querySelector('[data-save-id="' + saveId + '"]');
        if (el) el.innerHTML = state.editableTexts[saveId].html;
      });
    }

    // ── Restore background images — match by data-save-id ──
    if (state.images && typeof state.images === 'object' && !Array.isArray(state.images)) {
      Object.keys(state.images).forEach(function (saveId) {
        var el = document.querySelector('[data-save-id="' + saveId + '"]');
        if (!el) return;
        var saved = state.images[saveId];
        if (saved.bgImage) el.style.backgroundImage = saved.bgImage;
        if (saved.bgPosition) el.style.backgroundPosition = saved.bgPosition;
        if (saved.bgSize) el.style.backgroundSize = saved.bgSize;
        if (saved.bgX) el.setAttribute('data-bg-x', saved.bgX);
        if (saved.bgY) el.setAttribute('data-bg-y', saved.bgY);
        if (saved.bgZoom) el.setAttribute('data-bg-zoom', saved.bgZoom);
      });
    }

    // ── Restore map SVG — match by data-save-id ──
    if (state.mapSvg && typeof state.mapSvg === 'object') {
      Object.keys(state.mapSvg).forEach(function (saveId) {
        var mapArea = document.querySelector('[data-save-id="' + saveId + '"]');
        if (!mapArea) return;
        var content = (state.mapSvg[saveId] || {}).svgContent;
        if (!content) return;

        // Sanitise: only inject if content starts with '<svg'
        var trimmed = content.trimStart();
        if (trimmed.substring(0, 4).toLowerCase() !== '<svg') return;

        // Hide placeholder content
        mapArea.querySelectorAll('.ph-icon, .ph-label').forEach(function (e) {
          e.style.display = 'none';
        });
        var btn = mapArea.querySelector('.map-generate-btn');
        if (btn) btn.style.display = 'none';
        mapArea.classList.remove('img-placeholder');
        mapArea.innerHTML = content;
        mapArea.style.backgroundImage = 'none';
        mapArea.style.background = 'transparent';
        mapArea.classList.add('map-loaded');
        mapArea.style.border = 'none';
      });
    }

    // ── Restore V1 Map Pipeline style tokens ──
    if (state.mapStyleTokens) {
      window.__mapStyleTokens = state.mapStyleTokens;
      window.__mapStyleHash = state.mapStyleHash || '';
    }
  }

  function showIndicator(status) {
    if (!indicator) return;
    indicator.className = 'autosave-indicator ' + status;
    if (status === 'saving') indicator.textContent = 'Saving...';
    else if (status === 'saved') indicator.textContent = 'Saved';
    else if (status === 'error') indicator.textContent = 'Save failed';
    else if (status === 'editing') indicator.textContent = 'Unsaved';
  }

  function simpleHash(str) {
    var hash = 0;
    for (var i = 0; i < str.length; i++) {
      var chr = str.charCodeAt(i);
      hash = ((hash << 5) - hash) + chr;
      hash |= 0;
    }
    return String(hash);
  }
});
