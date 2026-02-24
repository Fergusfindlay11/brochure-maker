/**
 * AI Content Rewrite
 * Floating toolbar appears below the text formatting toolbar on text selection.
 * Buttons: Rewrite, Concise, Expand, Formal, Persuasive
 * Calls /api/ai/rewrite to get AI-rewritten text.
 */
document.addEventListener('DOMContentLoaded', function () {
  var toolbar = document.createElement('div');
  toolbar.id = 'aiRewriteToolbar';
  toolbar.className = 'ai-rewrite-toolbar';
  toolbar.innerHTML =
    '<button data-action="rewrite" title="Rewrite">Rewrite</button>' +
    '<span class="ai-divider"></span>' +
    '<button data-action="concise" title="Make Concise">Concise</button>' +
    '<button data-action="expand" title="Expand">Expand</button>' +
    '<button data-action="formal" title="Make Formal">Formal</button>' +
    '<button data-action="persuasive" title="Make Persuasive">Persuasive</button>';
  document.body.appendChild(toolbar);

  var isVisible = false;
  var previousText = '';
  var previousRange = null;

  // Show toolbar below text toolbar when selection exists in contenteditable
  document.addEventListener('selectionchange', function () {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      hideToolbar();
      return;
    }

    var anchor = sel.anchorNode;
    var editable = ancestor(anchor, '[contenteditable="true"]');
    if (!editable) {
      hideToolbar();
      return;
    }

    showToolbar(sel);
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

    // Position below the text toolbar (which is at top - 44px)
    var toolbarW = toolbar.offsetWidth;
    var left = rect.left + (rect.width / 2) - (toolbarW / 2) + window.scrollX;
    var top = rect.bottom + 8 + window.scrollY;

    left = Math.max(8, Math.min(left, window.innerWidth - toolbarW - 8));
    toolbar.style.left = left + 'px';
    toolbar.style.top = top + 'px';
  }

  function hideToolbar() {
    if (!isVisible) return;
    toolbar.style.display = 'none';
    isVisible = false;
  }

  // Handle button clicks
  toolbar.addEventListener('mousedown', function (e) {
    e.preventDefault();
    var btn = e.target.closest('button');
    if (!btn || btn.classList.contains('ai-loading')) return;

    var action = btn.getAttribute('data-action');
    if (!action) return;

    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;

    var selectedText = sel.toString().trim();
    if (!selectedText) return;

    var range = sel.getRangeAt(0);
    previousText = selectedText;
    previousRange = range.cloneRange();

    // Disable all buttons, show loading
    var buttons = toolbar.querySelectorAll('button');
    buttons.forEach(function (b) { b.classList.add('ai-loading'); });
    btn.textContent = '...';

    fetch('/api/ai/rewrite', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: selectedText, action: action }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error('Rewrite failed');
        return res.json();
      })
      .then(function (data) {
        if (data.rewritten) {
          // Replace the selected text
          try {
            previousRange.deleteContents();
            previousRange.insertNode(document.createTextNode(data.rewritten));
          } catch (err) {
            // Fallback: try execCommand
            document.execCommand('insertText', false, data.rewritten);
          }
          if (window.pushUndoState) window.pushUndoState();
        }
      })
      .catch(function (err) {
        // Show error via toast if available
        if (window.showBrochureToast) {
          window.showBrochureToast('AI rewrite error: ' + err.message);
        }
      })
      .finally(function () {
        buttons.forEach(function (b) { b.classList.remove('ai-loading'); });
        // Restore button labels
        toolbar.querySelectorAll('button').forEach(function (b) {
          var a = b.getAttribute('data-action');
          b.textContent = a.charAt(0).toUpperCase() + a.slice(1);
        });
        hideToolbar();
      });
  });

  function ancestor(node, selector) {
    if (!node) return null;
    var el = node.nodeType === 3 ? node.parentNode : node;
    while (el && el !== document) {
      if (el.matches && el.matches(selector)) return el;
      el = el.parentNode;
    }
    return null;
  }

  // Hide on click outside
  document.addEventListener('mousedown', function (e) {
    if (toolbar.contains(e.target)) return;
    setTimeout(function () {
      var sel = window.getSelection();
      if (!sel || sel.isCollapsed) hideToolbar();
    }, 100);
  });
});
