/**
 * AI Chat Sidebar — Context-Aware Edition
 * Tracks active slide (IntersectionObserver), text selection, and focused element.
 * Sends focused context to the AI so edits target the right slide/element.
 * Supports minimize/expand and context dismiss.
 */
document.addEventListener('DOMContentLoaded', function () {
  var projectId = window.__PROJECT_ID__;
  if (!projectId) return;

  var panel = document.getElementById('chatSidebarPanel');
  var toggleBtn = document.getElementById('chatToggleBtn');
  var chatMessages = document.getElementById('chatMessages');
  var chatInput = document.getElementById('chatInput');
  var chatSendBtn = document.getElementById('chatSendBtn');
  var chatCloseBtn = document.getElementById('chatCloseBtn');
  var minimizeBtn = document.getElementById('chatMinimizeBtn');
  var minimizedTab = document.getElementById('chatMinimizedTab');
  var ctxIndicator = document.getElementById('chatContextIndicator');
  var ctxText = document.getElementById('ctxText');
  var ctxDismiss = document.getElementById('ctxDismiss');
  if (!panel || !toggleBtn) return;

  var chatHistory = [];
  var historyLoaded = false;

  // ── Context tracking state ──
  var activeSlideId = null;
  var activeSlideLabel = null;
  var selectionContext = null;
  var contextMode = 'auto'; // 'auto' | 'all'

  // ── DOM helper ──
  function ancestor(node, selector) {
    if (!node) return null;
    var el = node.nodeType === 3 ? node.parentNode : node;
    while (el && el !== document) {
      if (el.matches && el.matches(selector)) return el;
      el = el.parentNode;
    }
    return null;
  }

  // ── Active slide tracking via IntersectionObserver ──
  var slideObserver = new IntersectionObserver(function (entries) {
    var best = null;
    var bestRatio = 0;
    entries.forEach(function (entry) {
      if (entry.isIntersecting && entry.intersectionRatio > bestRatio) {
        best = entry.target;
        bestRatio = entry.intersectionRatio;
      }
    });
    if (best && best.id !== activeSlideId) {
      activeSlideId = best.id;
      var prev = best.previousElementSibling;
      if (prev && prev.classList.contains('slide-label')) {
        activeSlideLabel = prev.textContent
          .replace(/^SLIDE\s*\d+\s*[—–\-]\s*/i, '')
          .trim();
      } else {
        activeSlideLabel = best.id;
      }
      updateContextIndicator();
    }
  }, { threshold: [0.3, 0.5, 0.7] });

  document.querySelectorAll('.slide-wrapper').forEach(function (wrapper) {
    slideObserver.observe(wrapper);
  });

  // Expose for other modules
  window.__activeSlide__ = function () { return activeSlideId; };
  window.__activeContext__ = function () { return selectionContext; };

  // ── Text selection tracking ──
  document.addEventListener('selectionchange', function () {
    var sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) {
      selectionContext = null;
      updateContextIndicator();
      return;
    }

    var anchor = sel.anchorNode;
    var editable = ancestor(anchor, '[contenteditable="true"]');
    if (!editable) {
      selectionContext = null;
      updateContextIndicator();
      return;
    }

    var slideWrapper = ancestor(editable, '.slide-wrapper');
    var slideId = slideWrapper ? slideWrapper.id : activeSlideId;
    var selectedText = sel.isCollapsed ? '' : sel.toString().trim();

    selectionContext = {
      slideId: slideId,
      elementClass: editable.className || '',
      elementTag: editable.tagName.toLowerCase(),
      elementId: editable.id || '',
      selectedText: selectedText,
      fullText: editable.textContent.trim()
    };

    // Update active slide if focus moved to a different slide
    if (slideId && slideId !== activeSlideId) {
      activeSlideId = slideId;
      if (slideWrapper) {
        var prev = slideWrapper.previousElementSibling;
        if (prev && prev.classList.contains('slide-label')) {
          activeSlideLabel = prev.textContent
            .replace(/^SLIDE\s*\d+\s*[—–\-]\s*/i, '')
            .trim();
        }
      }
    }

    updateContextIndicator();
  });

  // Track focus into contenteditables
  document.addEventListener('focusin', function (e) {
    var editable = ancestor(e.target, '[contenteditable="true"]');
    if (!editable) return;

    // Re-enable auto context when user interacts with content
    if (contextMode === 'all') contextMode = 'auto';

    var slideWrapper = ancestor(editable, '.slide-wrapper');
    if (slideWrapper && slideWrapper.id !== activeSlideId) {
      activeSlideId = slideWrapper.id;
      var prev = slideWrapper.previousElementSibling;
      if (prev && prev.classList.contains('slide-label')) {
        activeSlideLabel = prev.textContent
          .replace(/^SLIDE\s*\d+\s*[—–\-]\s*/i, '')
          .trim();
      }
      updateContextIndicator();
    }
  });

  // ── Context indicator UI ──
  function updateContextIndicator() {
    if (!ctxIndicator || contextMode === 'all') {
      if (ctxIndicator) ctxIndicator.style.display = 'none';
      return;
    }

    if (selectionContext && selectionContext.selectedText) {
      var truncated = selectionContext.selectedText.length > 30
        ? selectionContext.selectedText.substring(0, 30) + '\u2026'
        : selectionContext.selectedText;
      var slideNum = (selectionContext.slideId || '').replace('slide', '');
      ctxText.textContent = "Selected: \u2018" + truncated + "\u2019 in Slide " + slideNum;
      ctxIndicator.style.display = 'flex';
    } else if (activeSlideId) {
      var num = activeSlideId.replace('slide', '');
      var label = activeSlideLabel || 'Slide ' + num;
      ctxText.textContent = 'On: Slide ' + num + ' \u2014 ' + label;
      ctxIndicator.style.display = 'flex';
    } else {
      ctxIndicator.style.display = 'none';
    }
  }

  // Dismiss context
  if (ctxDismiss) {
    ctxDismiss.addEventListener('click', function () {
      contextMode = 'all';
      selectionContext = null;
      if (ctxIndicator) ctxIndicator.style.display = 'none';
    });
  }

  // ── Toggle sidebar ──
  toggleBtn.addEventListener('click', function () {
    // If minimized, expand from minimized state
    if (minimizedTab && minimizedTab.style.display !== 'none') {
      minimizedTab.style.display = 'none';
    }
    panel.classList.toggle('open');
    document.body.classList.toggle('chat-sidebar-open');
    if (panel.classList.contains('open') && !historyLoaded) {
      loadChatHistory();
      historyLoaded = true;
    }
    if (panel.classList.contains('open')) {
      chatInput.focus();
    }
  });

  if (chatCloseBtn) {
    chatCloseBtn.addEventListener('click', function () {
      panel.classList.remove('open');
      document.body.classList.remove('chat-sidebar-open');
      if (minimizedTab) minimizedTab.style.display = 'none';
    });
  }

  // ── Minimize / expand ──
  if (minimizeBtn) {
    minimizeBtn.addEventListener('click', function () {
      panel.classList.remove('open');
      document.body.classList.remove('chat-sidebar-open');
      if (minimizedTab) minimizedTab.style.display = 'flex';
    });
  }

  if (minimizedTab) {
    minimizedTab.addEventListener('click', function () {
      minimizedTab.style.display = 'none';
      panel.classList.add('open');
      document.body.classList.add('chat-sidebar-open');
      if (!historyLoaded) {
        loadChatHistory();
        historyLoaded = true;
      }
      chatInput.focus();
    });
  }

  // ── Send message ──
  chatSendBtn.addEventListener('click', sendMessage);
  chatInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  function sendMessage() {
    var message = chatInput.value.trim();
    if (!message) return;

    appendMessage('user', message);
    chatInput.value = '';
    chatInput.disabled = true;
    chatSendBtn.disabled = true;

    // Show typing indicator
    var typingDiv = document.createElement('div');
    typingDiv.className = 'chat-msg chat-msg-typing';
    typingDiv.textContent = 'Thinking\u2026';
    chatMessages.appendChild(typingDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    // Gather context (focused or all)
    var contextPayload = gatherBrochureContext();

    fetch('/api/projects/' + projectId + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: message,
        brochure_context: contextPayload.slides,
        selection_context: contextPayload.selection || null,
        active_slide_id: contextPayload.active_slide_id || null,
        history: chatHistory.slice(-10),
      }),
    })
    .then(function (res) {
      if (!res.ok) throw new Error('Chat request failed');
      return res.json();
    })
    .then(function (data) {
      if (typingDiv.parentNode) typingDiv.remove();
      appendMessage('assistant', data.reply || 'Done.');
      if (data.edits && data.edits.length > 0) {
        var applied = applyEdits(data.edits);
        if (applied > 0) {
          appendMessage('system', 'Applied ' + applied + ' edit(s) to the brochure.');
        }
      }
    })
    .catch(function (err) {
      if (typingDiv.parentNode) typingDiv.remove();
      appendMessage('system', 'Error: ' + err.message);
    })
    .finally(function () {
      chatInput.disabled = false;
      chatSendBtn.disabled = false;
      chatInput.focus();
    });
  }

  // ── Gather brochure context (focused or all) ──
  function gatherBrochureContext() {
    var slides = [];
    var allWrappers = document.querySelectorAll('.slide-wrapper');
    var useFocused = (contextMode === 'auto') && activeSlideId;

    allWrappers.forEach(function (wrapper) {
      if (wrapper.classList.contains('slide-hidden')) return;

      var isActive = useFocused && (wrapper.id === activeSlideId);
      var slideData = { id: wrapper.id, elements: [], is_active: isActive };

      if (useFocused && !isActive) {
        // Summary only for non-active slides
        var typeClass = '';
        wrapper.classList.forEach(function (cls) {
          if (cls.startsWith('slide-') && cls !== 'slide-wrapper' && cls !== 'slide-hidden') {
            typeClass = cls.replace('slide-', '');
          }
        });
        slideData.summary_only = true;
        slideData.slide_type = typeClass;
        slides.push(slideData);
        return;
      }

      // Full content for active slide (or all slides in 'all' mode)
      wrapper.querySelectorAll('[contenteditable="true"]').forEach(function (el) {
        slideData.elements.push({
          classes: el.className,
          tag: el.tagName.toLowerCase(),
          id: el.id || '',
          text: el.textContent.trim(),
        });
      });
      if (slideData.elements.length > 0) {
        slides.push(slideData);
      }
    });

    var payload = { slides: slides };

    if (useFocused) {
      payload.active_slide_id = activeSlideId;
      if (selectionContext && selectionContext.selectedText) {
        payload.selection = {
          slide_id: selectionContext.slideId,
          element_class: selectionContext.elementClass,
          element_tag: selectionContext.elementTag,
          selected_text: selectionContext.selectedText,
          full_element_text: selectionContext.fullText
        };
      }
    }

    return payload;
  }

  // ── Apply edits from AI response ──
  function applyEdits(edits) {
    var applied = 0;
    edits.forEach(function (edit) {
      var target = null;

      if (edit.selector) {
        try { target = document.querySelector(edit.selector); } catch (e) { /* invalid selector */ }
      }

      if (!target && edit.slide_id) {
        var slide = document.getElementById(edit.slide_id);
        if (slide) {
          if (edit.element_class) {
            target = slide.querySelector('.' + edit.element_class.split(' ')[0]);
          }
          if (!target && edit.element_tag) {
            target = slide.querySelector(edit.element_tag + '[contenteditable="true"]');
          }
        }
      }

      if (target && edit.new_html !== undefined) {
        target.innerHTML = edit.new_html;
        applied++;
      }
    });

    if (applied > 0 && window.pushUndoState) {
      window.pushUndoState();
    }
    return applied;
  }

  // ── Chat message display + history ──
  function appendMessage(role, text) {
    if (role !== 'system') {
      chatHistory.push({ role: role, content: text });
    }

    var msgDiv = document.createElement('div');
    msgDiv.className = 'chat-msg chat-msg-' + role;
    msgDiv.textContent = text;
    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    saveChatHistory();
  }

  function loadChatHistory() {
    fetch('/api/projects/' + projectId + '/chat/history')
      .then(function (res) { return res.ok ? res.json() : { messages: [] }; })
      .then(function (data) {
        if (data.messages && data.messages.length > 0) {
          data.messages.forEach(function (msg) {
            var role = msg.role || 'system';
            var content = msg.content || '';
            if (role !== 'system') {
              chatHistory.push({ role: role, content: content });
            }
            var msgDiv = document.createElement('div');
            msgDiv.className = 'chat-msg chat-msg-' + role;
            msgDiv.textContent = content;
            chatMessages.appendChild(msgDiv);
          });
          chatMessages.scrollTop = chatMessages.scrollHeight;
        }
      })
      .catch(function () { /* No history, fine */ });
  }

  function saveChatHistory() {
    var allMessages = [];
    chatMessages.querySelectorAll('.chat-msg').forEach(function (el) {
      var role = 'system';
      if (el.classList.contains('chat-msg-user')) role = 'user';
      else if (el.classList.contains('chat-msg-assistant')) role = 'assistant';
      allMessages.push({ role: role, content: el.textContent });
    });

    fetch('/api/projects/' + projectId + '/chat/history', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: allMessages }),
    }).catch(function () { /* Best effort */ });
  }
});
