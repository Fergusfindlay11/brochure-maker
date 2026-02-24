/**
 * Auto-Save & State Persistence
 * Saves editor state (all contenteditable text, images, styles) every 30s.
 * Restores state on page load if available.
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

    // Capture all contenteditable text content
    var editables = document.querySelectorAll('[contenteditable="true"]');
    var texts = [];
    editables.forEach(function (el) {
      var slideWrapper = el.closest('.slide-wrapper');
      var slideId = slideWrapper ? slideWrapper.id : '';
      var classList = Array.from(el.classList).join(' ');
      texts.push({
        slideId: slideId,
        classes: classList,
        html: el.innerHTML,
      });
    });
    state.editableTexts = texts;

    // Capture background images and positions
    var images = [];
    document.querySelectorAll('.img-placeholder').forEach(function (el) {
      var slideWrapper = el.closest('.slide-wrapper');
      images.push({
        slideId: slideWrapper ? slideWrapper.id : '',
        bgImage: el.style.backgroundImage || '',
        bgPosition: el.style.backgroundPosition || '',
        bgSize: el.style.backgroundSize || '',
        bgX: el.getAttribute('data-bg-x') || '',
        bgY: el.getAttribute('data-bg-y') || '',
        bgZoom: el.getAttribute('data-bg-zoom') || '',
      });
    });
    state.images = images;

    // Capture colour scheme
    var root = document.documentElement;
    state.primaryColour = getComputedStyle(root).getPropertyValue('--primary').trim();

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
    // Restore editable text content
    if (state.editableTexts) {
      var editables = document.querySelectorAll('[contenteditable="true"]');
      state.editableTexts.forEach(function (saved, idx) {
        // Match by index (order should be stable for same slide structure)
        if (editables[idx]) {
          editables[idx].innerHTML = saved.html;
        }
      });
    }

    // Restore background images
    if (state.images) {
      var placeholders = document.querySelectorAll('.img-placeholder');
      state.images.forEach(function (saved, idx) {
        if (placeholders[idx] && saved.bgImage) {
          placeholders[idx].style.backgroundImage = saved.bgImage;
          if (saved.bgPosition) placeholders[idx].style.backgroundPosition = saved.bgPosition;
          if (saved.bgSize) placeholders[idx].style.backgroundSize = saved.bgSize;
          if (saved.bgX) placeholders[idx].setAttribute('data-bg-x', saved.bgX);
          if (saved.bgY) placeholders[idx].setAttribute('data-bg-y', saved.bgY);
          if (saved.bgZoom) placeholders[idx].setAttribute('data-bg-zoom', saved.bgZoom);
        }
      });
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
