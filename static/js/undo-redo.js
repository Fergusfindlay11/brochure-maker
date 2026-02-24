/**
 * Undo/Redo System
 * Captures DOM snapshots of the slides container on changes.
 * Ctrl+Z / Cmd+Z to undo, Ctrl+Shift+Z / Cmd+Shift+Z to redo.
 */
document.addEventListener('DOMContentLoaded', function () {
  var MAX_HISTORY = 50;
  var history = [];
  var pointer = -1;
  var isRestoring = false;
  var debounceTimer = null;
  var slidesContainer = null;

  // Find the slides container (parent of .slide-wrapper elements)
  var firstSlide = document.querySelector('.slide-wrapper');
  if (firstSlide) {
    slidesContainer = firstSlide.parentNode;
  }
  if (!slidesContainer) return;

  // Capture initial state
  pushState();

  // Expose globally so other modules can trigger snapshots
  window.pushUndoState = function () {
    if (isRestoring) return;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(pushState, 300);
  };

  // MutationObserver to detect DOM changes
  var observer = new MutationObserver(function (mutations) {
    if (isRestoring) return;
    // Ignore mutations from slide-manager-bar or text-toolbar
    var dominated = mutations.every(function (m) {
      var t = m.target;
      if (!t || !t.closest) return false;
      return t.closest('.slide-manager-bar') || t.closest('.text-toolbar') ||
             t.closest('.ai-rewrite-toolbar') || t.closest('.autosave-indicator');
    });
    if (dominated) return;
    window.pushUndoState();
  });

  observer.observe(slidesContainer, {
    childList: true,
    subtree: true,
    attributes: true,
    characterData: true,
    attributeFilter: ['style', 'class', 'contenteditable', 'data-bg-x', 'data-bg-y', 'data-bg-zoom'],
  });

  function pushState() {
    if (isRestoring) return;
    var snapshot = slidesContainer.innerHTML;
    // Don't push if identical to current state
    if (pointer >= 0 && history[pointer] === snapshot) return;
    // Truncate forward history
    history = history.slice(0, pointer + 1);
    history.push(snapshot);
    // Limit history size
    if (history.length > MAX_HISTORY) {
      history.shift();
    }
    pointer = history.length - 1;
  }

  function undo() {
    if (pointer <= 0) return;
    pointer--;
    restore(history[pointer]);
  }

  function redo() {
    if (pointer >= history.length - 1) return;
    pointer++;
    restore(history[pointer]);
  }

  function restore(snapshot) {
    isRestoring = true;
    observer.disconnect();
    slidesContainer.innerHTML = snapshot;
    // Re-observe
    observer.observe(slidesContainer, {
      childList: true,
      subtree: true,
      attributes: true,
      characterData: true,
      attributeFilter: ['style', 'class', 'contenteditable', 'data-bg-x', 'data-bg-y', 'data-bg-zoom'],
    });
    isRestoring = false;
  }

  // Keyboard shortcuts
  document.addEventListener('keydown', function (e) {
    var isMod = e.ctrlKey || e.metaKey;
    if (!isMod) return;

    if (e.key === 'z' && !e.shiftKey) {
      e.preventDefault();
      undo();
    } else if ((e.key === 'z' && e.shiftKey) || e.key === 'y') {
      e.preventDefault();
      redo();
    }
  });
});
