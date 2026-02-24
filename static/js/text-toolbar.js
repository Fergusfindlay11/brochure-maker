/**
 * Rich Text Formatting Toolbar
 * Floating toolbar appears on text selection within contenteditable elements.
 * Buttons: Bold, Italic, Underline, Bullet List, Font Size +/-
 */
document.addEventListener('DOMContentLoaded', function () {
  // Create toolbar element
  var toolbar = document.createElement('div');
  toolbar.id = 'textToolbar';
  toolbar.className = 'text-toolbar';
  toolbar.innerHTML =
    '<button data-cmd="bold" title="Bold (Ctrl+B)"><b>B</b></button>' +
    '<button data-cmd="italic" title="Italic (Ctrl+I)"><i>I</i></button>' +
    '<button data-cmd="underline" title="Underline (Ctrl+U)"><u>U</u></button>' +
    '<span class="tt-divider"></span>' +
    '<button data-cmd="insertUnorderedList" title="Bullet List">&#8226;</button>' +
    '<span class="tt-divider"></span>' +
    '<button data-cmd="fontSize" data-dir="-1" title="Decrease font size">A&#8595;</button>' +
    '<button data-cmd="fontSize" data-dir="1" title="Increase font size">A&#8593;</button>';
  document.body.appendChild(toolbar);

  var isVisible = false;

  // Show/hide toolbar on selection change
  document.addEventListener('selectionchange', function () {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      hideToolbar();
      return;
    }

    // Check if selection is inside a contenteditable element
    var anchor = sel.anchorNode;
    var editable = ancestor(anchor, '[contenteditable="true"]');
    if (!editable) {
      hideToolbar();
      return;
    }

    showToolbar(sel);
  });

  // Handle toolbar button clicks
  toolbar.addEventListener('mousedown', function (e) {
    e.preventDefault(); // Prevent losing selection
    var btn = e.target.closest('button');
    if (!btn) return;

    var cmd = btn.getAttribute('data-cmd');
    if (!cmd) return;

    if (cmd === 'fontSize') {
      var dir = parseInt(btn.getAttribute('data-dir'), 10);
      adjustFontSize(dir);
    } else {
      document.execCommand(cmd, false, null);
    }

    // Update active states
    updateActiveStates();
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', function (e) {
    var editable = ancestor(document.activeElement || e.target, '[contenteditable="true"]');
    if (!editable) return;

    var key = e.key ? e.key.toLowerCase() : '';
    var isMod = e.ctrlKey || e.metaKey;

    if (isMod && key === 'b') {
      e.preventDefault();
      document.execCommand('bold', false, null);
      updateActiveStates();
    } else if (isMod && key === 'i') {
      e.preventDefault();
      document.execCommand('italic', false, null);
      updateActiveStates();
    } else if (isMod && key === 'u') {
      e.preventDefault();
      document.execCommand('underline', false, null);
      updateActiveStates();
    }
  });

  function showToolbar(sel) {
    var range = sel.getRangeAt(0);
    var rect = range.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) {
      hideToolbar();
      return;
    }

    toolbar.style.display = 'flex';
    isVisible = true;

    // Position above selection, centered
    var toolbarW = toolbar.offsetWidth;
    var left = rect.left + (rect.width / 2) - (toolbarW / 2) + window.scrollX;
    var top = rect.top - 44 + window.scrollY;

    // Keep within viewport
    left = Math.max(8, Math.min(left, window.innerWidth - toolbarW - 8));
    if (top < 8) top = rect.bottom + 8 + window.scrollY;

    toolbar.style.left = left + 'px';
    toolbar.style.top = top + 'px';

    updateActiveStates();
  }

  function hideToolbar() {
    if (!isVisible) return;
    toolbar.style.display = 'none';
    isVisible = false;
  }

  function updateActiveStates() {
    var buttons = toolbar.querySelectorAll('button[data-cmd]');
    buttons.forEach(function (btn) {
      var cmd = btn.getAttribute('data-cmd');
      if (cmd === 'fontSize') return;
      try {
        btn.classList.toggle('active', document.queryCommandState(cmd));
      } catch (e) { /* ignore */ }
    });
  }

  function adjustFontSize(direction) {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;

    var range = sel.getRangeAt(0);
    var container = range.commonAncestorContainer;
    if (container.nodeType === 3) container = container.parentNode;

    // Get current font size
    var computed = window.getComputedStyle(container);
    var current = parseFloat(computed.fontSize) || 16;
    var step = direction > 0 ? 2 : -2;
    var newSize = Math.max(8, Math.min(72, current + step));

    // Wrap selection in a span with the new size
    var span = document.createElement('span');
    span.style.fontSize = newSize + 'px';
    try {
      range.surroundContents(span);
    } catch (e) {
      // If surroundContents fails (partial selections), use execCommand fallback
      document.execCommand('fontSize', false, '7');
      // Replace the generated <font size="7"> tags with proper size
      var editable = ancestor(container, '[contenteditable="true"]');
      if (editable) {
        editable.querySelectorAll('font[size="7"]').forEach(function (font) {
          font.removeAttribute('size');
          font.style.fontSize = newSize + 'px';
        });
      }
    }
  }

  function ancestor(node, selector) {
    if (!node) return null;
    var el = node.nodeType === 3 ? node.parentNode : node;
    while (el && el !== document) {
      if (el.matches && el.matches(selector)) return el;
      el = el.parentNode;
    }
    return null;
  }

  // Hide toolbar on click outside
  document.addEventListener('mousedown', function (e) {
    if (toolbar.contains(e.target)) return;
    // Delay to let selectionchange fire
    setTimeout(function () {
      var sel = window.getSelection();
      if (!sel || sel.isCollapsed) hideToolbar();
    }, 100);
  });
});
