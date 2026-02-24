/**
 * Slide Reorder, Duplicate, Delete & Hide/Show
 * Adds a floating toolbar to each slide with Move Up, Move Down, Duplicate, Delete, Hide.
 */
document.addEventListener('DOMContentLoaded', function () {
  // Inject manager bars into all slides
  var slides = document.querySelectorAll('.slide-wrapper');
  slides.forEach(function (slide) { injectBar(slide); });

  function injectBar(slide) {
    // Skip if already has bar
    if (slide.querySelector('.slide-manager-bar')) return;
    var isHidden = slide.classList.contains('slide-hidden');
    var bar = document.createElement('div');
    bar.className = 'slide-manager-bar';
    bar.innerHTML =
      '<button class="sm-hide" title="Hide/Show slide">' + (isHidden ? '&#9673;' : '&#9675;') + '</button>' +
      '<button class="sm-up" title="Move Up">&#9650;</button>' +
      '<button class="sm-down" title="Move Down">&#9660;</button>' +
      '<button class="sm-dup" title="Duplicate">&#10697;</button>' +
      '<button class="sm-delete" title="Delete">&#10005;</button>';
    slide.appendChild(bar);
  }

  // Use event delegation for slide manager actions
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.slide-manager-bar button');
    if (!btn) return;
    var slide = btn.closest('.slide-wrapper');
    if (!slide) return;

    e.preventDefault();
    e.stopPropagation();

    if (btn.classList.contains('sm-up')) moveSlide(slide, -1);
    else if (btn.classList.contains('sm-down')) moveSlide(slide, 1);
    else if (btn.classList.contains('sm-dup')) duplicateSlide(slide);
    else if (btn.classList.contains('sm-delete')) deleteSlide(slide);
    else if (btn.classList.contains('sm-hide')) toggleHideSlide(slide, btn);
  });

  function moveSlide(slide, direction) {
    var parent = slide.parentNode;
    var siblings = Array.from(parent.querySelectorAll('.slide-wrapper'));
    var idx = siblings.indexOf(slide);
    var targetIdx = idx + direction;

    if (targetIdx < 0 || targetIdx >= siblings.length) return;

    if (direction === -1) {
      parent.insertBefore(slide, siblings[targetIdx]);
    } else {
      var next = siblings[targetIdx].nextElementSibling;
      if (next) {
        parent.insertBefore(slide, next);
      } else {
        parent.appendChild(slide);
      }
    }

    renumberSlides();
    syncNavBar();
    if (window.pushUndoState) window.pushUndoState();
  }

  function duplicateSlide(slide) {
    var clone = slide.cloneNode(true);
    // Remove old manager bar and re-inject (to get fresh listeners via delegation)
    var oldBar = clone.querySelector('.slide-manager-bar');
    if (oldBar) oldBar.remove();
    injectBar(clone);

    // Insert after current slide
    slide.parentNode.insertBefore(clone, slide.nextElementSibling);
    renumberSlides();
    syncNavBar();

    // Scroll to the new slide
    clone.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (window.pushUndoState) window.pushUndoState();
  }

  function deleteSlide(slide) {
    var siblings = slide.parentNode.querySelectorAll('.slide-wrapper');
    if (siblings.length <= 1) return; // Don't delete last slide

    slide.remove();
    renumberSlides();
    syncNavBar();
    if (window.pushUndoState) window.pushUndoState();
  }

  function toggleHideSlide(slide, btn) {
    var isHidden = slide.classList.toggle('slide-hidden');
    // Also toggle the preceding slide-label
    var prev = slide.previousElementSibling;
    if (prev && prev.classList.contains('slide-label')) {
      prev.classList.toggle('slide-hidden', isHidden);
    }
    // Update button icon: filled circle = hidden, empty circle = visible
    btn.innerHTML = isHidden ? '&#9673;' : '&#9675;';
    btn.title = isHidden ? 'Show slide' : 'Hide slide';
    syncNavBar();
    if (window.pushUndoState) window.pushUndoState();
  }

  function renumberSlides() {
    var slides = document.querySelectorAll('.slide-wrapper');
    slides.forEach(function (slide, idx) {
      slide.id = 'slide' + (idx + 1);
      // Update slide label if present
      var label = slide.querySelector('.slide-label');
      if (label) {
        var text = label.textContent.replace(/\d+/, String(idx + 1));
        label.textContent = text;
      }
    });
  }

  function syncNavBar() {
    var panel = document.getElementById('slidesDropdownPanel');
    if (!panel) return;

    // Clear existing items
    panel.innerHTML = '';

    // Build new items from current slides
    var slides = document.querySelectorAll('.slide-wrapper');
    slides.forEach(function (slide, idx) {
      var isHidden = slide.classList.contains('slide-hidden');
      var prev = slide.previousElementSibling;
      var labelText = '';
      if (prev && prev.classList.contains('slide-label')) {
        labelText = prev.textContent.replace(/^SLIDE\s*\d+\s*[—–-]\s*/i, '').trim();
      }
      if (!labelText) labelText = 'Slide ' + (idx + 1);

      var item = document.createElement('div');
      item.className = 'slides-dropdown-item' + (isHidden ? ' hidden-slide' : '');
      item.setAttribute('data-slide-index', idx + 1);

      item.innerHTML =
        '<label class="slides-dropdown-toggle">' +
          '<input type="checkbox"' + (isHidden ? '' : ' checked') + ' data-slide-target="slide' + (idx + 1) + '">' +
        '</label>' +
        '<a href="#slide' + (idx + 1) + '" class="slides-dropdown-link">' + labelText + '</a>';

      panel.appendChild(item);
    });
  }

  // ── Slides Dropdown Handlers ──
  var dropdownBtn = document.getElementById('slidesDropdownBtn');
  var dropdownPanel = document.getElementById('slidesDropdownPanel');
  if (dropdownBtn && dropdownPanel) {
    dropdownBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      dropdownPanel.classList.toggle('open');
    });

    // Close on click outside
    document.addEventListener('click', function (e) {
      if (!dropdownPanel.contains(e.target) && e.target !== dropdownBtn) {
        dropdownPanel.classList.remove('open');
      }
    });

    // Checkbox toggles slide visibility
    dropdownPanel.addEventListener('change', function (e) {
      var checkbox = e.target;
      if (!checkbox.matches('input[data-slide-target]')) return;
      var targetId = checkbox.getAttribute('data-slide-target');
      var slide = document.getElementById(targetId);
      if (!slide) return;
      var btn = slide.querySelector('.sm-hide');
      if (btn) {
        toggleHideSlide(slide, btn);
      } else {
        // Fallback: toggle directly
        var isHidden = slide.classList.toggle('slide-hidden');
        var prev = slide.previousElementSibling;
        if (prev && prev.classList.contains('slide-label')) {
          prev.classList.toggle('slide-hidden', isHidden);
        }
        if (window.pushUndoState) window.pushUndoState();
      }
    });

    // Link click scrolls and closes dropdown
    dropdownPanel.addEventListener('click', function (e) {
      var link = e.target.closest('.slides-dropdown-link');
      if (link) {
        dropdownPanel.classList.remove('open');
      }
    });
  }
});
