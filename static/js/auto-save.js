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
  function assignSaveIds(root) {
    root = root || document;
    var slides = [];
    if (root.matches && root.matches('.slide-wrapper')) {
      slides = [root];
    } else {
      slides = Array.from(root.querySelectorAll('.slide-wrapper'));
    }

    slides.forEach(function (slide) {
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
  }

  window.assignBrochureSaveIds = assignSaveIds;
  window.requestBrochureAutoSave = function (immediate) {
    if (debounceTimer) clearTimeout(debounceTimer);
    if (immediate) {
      saveState();
    } else {
      debounceTimer = setTimeout(saveState, DEBOUNCE_MS);
      showIndicator('editing');
    }
  };
  window.serializeBrochureEditorState = serializeState;

  assignSaveIds(document);

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
      attributeFilter: [
        'style',
        'data-bg-x',
        'data-bg-y',
        'data-bg-zoom',
        'data-slot-id',
        'data-layout-id',
        'data-layout-variant',
        'data-slide-instance-id',
        'data-slide-type',
      ],
    });
  }

  // Save before page unload
  window.addEventListener('beforeunload', function () {
    saveStateSync();
  });

  function serializeState() {
    assignSaveIds(document);
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
    var mapV1 = {};
    document.querySelectorAll('.map-area').forEach(function (el) {
      var saveId = el.getAttribute('data-save-id');
      if (!saveId) return;
      if (el.classList.contains('map-loaded')) {
        var svg = el.querySelector('svg');
        if (svg) {
          mapSvg[saveId] = { svgContent: svg.outerHTML };
        }
      }

      var mapId = el.getAttribute('data-map-v1-id') || '';
      var styleHash = el.getAttribute('data-map-v1-style-hash') || '';
      var styleVersion = el.getAttribute('data-map-v1-style-version') || '';
      var styleKey = el.getAttribute('data-map-v1-style-key') || '';
      var styleTokensRaw = el.getAttribute('data-map-v1-style-tokens') || '';
      var svgUrl = el.getAttribute('data-map-v1-svg-url') || '';
      var pdfUrl = el.getAttribute('data-map-v1-pdf-url') || '';
      var warningFlags = el.getAttribute('data-map-v1-warning-flags') || '';
      var needsNudge = el.getAttribute('data-map-v1-needs-nudge') || '';
      var metadataRaw = el.getAttribute('data-map-v1-metadata') || '';
      var warningsRaw = el.getAttribute('data-map-v1-warnings') || '';

      if (mapId || styleHash || styleTokensRaw || svgUrl || pdfUrl) {
        var styleTokensObj = null;
        var metadataObj = null;
        var warningsObj = null;
        try { styleTokensObj = styleTokensRaw ? JSON.parse(styleTokensRaw) : null; } catch (_) {}
        try { metadataObj = metadataRaw ? JSON.parse(metadataRaw) : null; } catch (_) {}
        try { warningsObj = warningsRaw ? JSON.parse(warningsRaw) : null; } catch (_) {}
        mapV1[saveId] = {
          mapId: mapId,
          styleHash: styleHash,
          styleVersion: styleVersion,
          styleKey: styleKey,
          styleTokens: styleTokensObj,
          artifacts: { svgUrl: svgUrl, pdfUrl: pdfUrl },
          warningFlags: warningFlags,
          needsNudge: needsNudge,
          metadata: metadataObj,
          warnings: warningsObj,
        };
      }
    });
    state.mapSvg = mapSvg;
    state.mapV1 = mapV1;

    // Capture colour scheme
    var root = document.documentElement;
    state.primaryColour = getComputedStyle(root).getPropertyValue('--primary').trim();

    // Capture layout swap state maintained by slide-manager.js. This contains
    // per-slide/per-layout slot snapshots so switching back can recover overflow.
    if (window.__brochureLayoutState) {
      state.layoutVariants = window.__brochureLayoutState;
    }

    if (window.getBrochureLogoState) {
      state.logo = window.getBrochureLogoState();
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
    if (state.primaryColour) {
      if (window.applyColorToAll) {
        window.applyColorToAll(state.primaryColour, { skipAutosave: true, silent: true });
      } else {
        document.documentElement.style.setProperty('--primary', state.primaryColour);
        document.querySelectorAll('.slide-wrapper').forEach(function (slide) {
          slide.style.background = state.primaryColour;
        });
      }
    }

    if (state.layoutVariants && typeof state.layoutVariants === 'object') {
      window.__brochureLayoutState = state.layoutVariants;
    }

    if (state.logo && window.applyBrochureLogoState) {
      window.applyBrochureLogoState(state.logo);
    }

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

    // ── Restore map-v1 metadata/artifact refs ──
    if (state.mapV1 && typeof state.mapV1 === 'object') {
      Object.keys(state.mapV1).forEach(function (saveId) {
        var mapArea = document.querySelector('[data-save-id="' + saveId + '"]');
        if (!mapArea) return;
        var entry = state.mapV1[saveId] || {};

        if (entry.mapId) mapArea.setAttribute('data-map-v1-id', entry.mapId);
        if (entry.styleHash) mapArea.setAttribute('data-map-v1-style-hash', entry.styleHash);
        if (entry.styleVersion) mapArea.setAttribute('data-map-v1-style-version', entry.styleVersion);
        if (entry.styleKey) mapArea.setAttribute('data-map-v1-style-key', entry.styleKey);
        if (entry.styleTokens) mapArea.setAttribute('data-map-v1-style-tokens', JSON.stringify(entry.styleTokens));
        if (entry.artifacts && entry.artifacts.svgUrl) mapArea.setAttribute('data-map-v1-svg-url', entry.artifacts.svgUrl);
        if (entry.artifacts && entry.artifacts.pdfUrl) mapArea.setAttribute('data-map-v1-pdf-url', entry.artifacts.pdfUrl);
        if (entry.warningFlags) mapArea.setAttribute('data-map-v1-warning-flags', entry.warningFlags);
        if (entry.needsNudge) mapArea.setAttribute('data-map-v1-needs-nudge', entry.needsNudge);
        if (entry.metadata) mapArea.setAttribute('data-map-v1-metadata', JSON.stringify(entry.metadata));
        if (entry.warnings) mapArea.setAttribute('data-map-v1-warnings', JSON.stringify(entry.warnings));

        var hasSvg = !!mapArea.querySelector('svg');
        if (!hasSvg && entry.artifacts && entry.artifacts.svgUrl) {
          fetch(entry.artifacts.svgUrl)
            .then(function (res) { if (!res.ok) return null; return res.text(); })
            .then(function (content) {
              if (!content) return;
              var trimmed = content.trimStart();
              if (trimmed.substring(0, 4).toLowerCase() !== '<svg') return;
              mapArea.querySelectorAll('.ph-icon, .ph-label').forEach(function (e) { e.style.display = 'none'; });
              var btn = mapArea.querySelector('.map-generate-btn');
              if (btn) btn.style.display = 'none';
              mapArea.classList.remove('img-placeholder');
              mapArea.innerHTML = content;
              mapArea.style.backgroundImage = 'none';
              mapArea.style.background = 'transparent';
              mapArea.classList.add('map-loaded');
              mapArea.style.border = 'none';
            })
            .catch(function () {
              // Ignore restore failures; user can regenerate map.
            });
        }
      });
    }

    document.dispatchEvent(new CustomEvent('brochure:state-restored', {
      detail: { state: state },
    }));
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
