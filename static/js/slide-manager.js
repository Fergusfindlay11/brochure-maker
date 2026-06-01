/**
 * Slide Reorder, Duplicate, Delete, Hide/Show, and Layout Swapping.
 * Uses delegated click handlers so newly rendered slides keep editor controls.
 */
document.addEventListener('DOMContentLoaded', function () {
  var LAYOUT_STATE_VERSION = 1;
  var layoutState = window.__brochureLayoutState || { version: LAYOUT_STATE_VERSION, slides: {} };
  var layoutMeta = null;
  var activeChooser = null;

  syncLayoutState();

  document.querySelectorAll('.slide-wrapper').forEach(function (slide) {
    prepareSlide(slide);
  });

  function prepareSlide(slide) {
    if (!slide) return;
    if (!slide.getAttribute('data-slide-type')) {
      slide.setAttribute('data-slide-type', getSlideType(slide));
    }
    ensureSlideIdentity(slide);
    ensureRuntimeSlotIds(slide);
    injectBar(slide);
    if (window.assignBrochureSaveIds) window.assignBrochureSaveIds(slide);
  }

  function injectBar(slide) {
    if (slide.querySelector('.slide-manager-bar')) return;
    var isHidden = slide.classList.contains('slide-hidden');
    var bar = document.createElement('div');
    bar.className = 'slide-manager-bar';
    bar.innerHTML =
      '<button class="sm-hide" title="Hide/Show slide">' + (isHidden ? '&#9673;' : '&#9675;') + '</button>' +
      '<button class="sm-up" title="Move Up">&#9650;</button>' +
      '<button class="sm-down" title="Move Down">&#9660;</button>' +
      '<button class="sm-layout" title="Swap slide layout" style="width:54px;font-size:10px;letter-spacing:0.02em;">Layout</button>' +
      '<button class="sm-dup" title="Duplicate">&#10697;</button>' +
      '<button class="sm-delete" title="Delete">&#10005;</button>';
    slide.appendChild(bar);
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.slide-manager-bar button');
    if (!btn) {
      if (activeChooser && !e.target.closest('.layout-chooser')) closeLayoutChooser();
      return;
    }

    var slide = btn.closest('.slide-wrapper');
    if (!slide) return;

    e.preventDefault();
    e.stopPropagation();

    if (btn.classList.contains('sm-up')) moveSlide(slide, -1);
    else if (btn.classList.contains('sm-down')) moveSlide(slide, 1);
    else if (btn.classList.contains('sm-layout')) openLayoutChooser(slide, btn);
    else if (btn.classList.contains('sm-dup')) duplicateSlide(slide);
    else if (btn.classList.contains('sm-delete')) deleteSlide(slide);
    else if (btn.classList.contains('sm-hide')) toggleHideSlide(slide, btn);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeLayoutChooser();
  });

  document.addEventListener('brochure:state-restored', function () {
    syncLayoutState();
    restoreSavedLayoutsFromState();
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
    requestAutosave();
    if (window.pushUndoState) window.pushUndoState();
  }

  function duplicateSlide(slide) {
    var clone = slide.cloneNode(true);
    var oldBar = clone.querySelector('.slide-manager-bar');
    if (oldBar) oldBar.remove();
    clone.removeAttribute('data-slide-instance-id');

    slide.parentNode.insertBefore(clone, slide.nextElementSibling);
    renumberSlides();
    prepareSlide(clone);
    syncNavBar();
    requestAutosave();

    clone.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (window.pushUndoState) window.pushUndoState();
  }

  function deleteSlide(slide) {
    var siblings = slide.parentNode.querySelectorAll('.slide-wrapper');
    if (siblings.length <= 1) return;

    slide.remove();
    renumberSlides();
    syncNavBar();
    requestAutosave();
    if (window.pushUndoState) window.pushUndoState();
  }

  function toggleHideSlide(slide, btn) {
    var isHidden = slide.classList.toggle('slide-hidden');
    var label = getSlideLabel(slide);
    if (label) label.classList.toggle('slide-hidden', isHidden);

    btn.innerHTML = isHidden ? '&#9673;' : '&#9675;';
    btn.title = isHidden ? 'Show slide' : 'Hide slide';
    syncNavBar();
    requestAutosave();
    if (window.pushUndoState) window.pushUndoState();
  }

  function openLayoutChooser(slide, anchorBtn) {
    closeLayoutChooser();

    var variants = getVariantsForSlide(slide);
    var currentLayout = getCurrentLayoutId(slide);
    var panel = document.createElement('div');
    panel.className = 'layout-chooser';
    panel.setAttribute('data-layout-editor-only', 'true');
    panel.style.cssText = [
      'position:fixed',
      'z-index:10000',
      'width:230px',
      'max-width:calc(100vw - 24px)',
      'background:rgba(30,24,20,0.97)',
      'border:1px solid rgba(255,255,255,0.18)',
      'box-shadow:0 18px 45px rgba(0,0,0,0.35)',
      'padding:8px',
      'border-radius:6px',
      'font-family:Jost,Arial,sans-serif',
      'color:white'
    ].join(';');

    if (!variants.length) {
      panel.innerHTML =
        '<div style="font-size:10px;letter-spacing:0.12em;text-transform:uppercase;color:rgba(255,255,255,0.55);margin-bottom:6px;">Layouts</div>' +
        '<div style="font-size:12px;line-height:1.35;color:rgba(255,255,255,0.78);">No layout variants exposed for this slide yet.</div>';
    } else {
      var title = document.createElement('div');
      title.textContent = 'Layouts';
      title.style.cssText = 'font-size:10px;letter-spacing:0.12em;text-transform:uppercase;color:rgba(255,255,255,0.55);margin:0 0 6px;';
      panel.appendChild(title);

      variants.forEach(function (variant) {
        var option = document.createElement('button');
        var isCurrent = variant.id === currentLayout;
        var imageCount = getVariantImageCount(variant);
        var imageCountChip = imageCount ? buildLayoutChipHtml(formatImageCount(imageCount), 'count') : '';
        var currentChip = isCurrent ? buildLayoutChipHtml('Current', 'current') : '';
        option.type = 'button';
        option.disabled = isCurrent;
        option.setAttribute('data-layout-id', variant.id);
        if (isCurrent) option.setAttribute('aria-current', 'true');
        option.style.cssText = [
          'display:block',
          'width:100%',
          'text-align:left',
          'border:1px solid ' + (isCurrent ? 'rgba(255,255,255,0.32)' : 'rgba(255,255,255,0.09)'),
          'border-radius:4px',
          'padding:6px 7px',
          'margin:0 0 4px',
          'cursor:' + (isCurrent ? 'default' : 'pointer'),
          'background:' + (isCurrent ? 'rgba(255,255,255,0.16)' : 'rgba(255,255,255,0.07)'),
          'color:white',
          'font-family:Jost,Arial,sans-serif',
          'opacity:1'
        ].join(';');
        option.innerHTML =
          '<span style="display:flex;align-items:center;gap:8px;min-width:0;">' +
            buildLayoutPreviewHtml(variant) +
            '<span style="display:block;min-width:0;flex:1;">' +
              '<span style="display:flex;align-items:center;gap:5px;min-width:0;">' +
                '<span style="display:block;min-width:0;flex:1;font-size:12px;letter-spacing:0.03em;line-height:1.2;overflow-wrap:anywhere;">' + escapeHtml(variant.label || variant.id) + '</span>' +
                imageCountChip +
                currentChip +
              '</span>' +
              (variant.description ? '<span style="display:block;font-size:10px;line-height:1.25;color:rgba(255,255,255,0.62);margin-top:3px;">' + escapeHtml(variant.description) + '</span>' : '') +
            '</span>' +
          '</span>';
        option.addEventListener('click', function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          if (!option.disabled) swapSlideLayout(slide, variant, anchorBtn);
        });
        panel.appendChild(option);
      });
    }

    document.body.appendChild(panel);
    positionChooser(panel, anchorBtn);
    activeChooser = { panel: panel, slide: slide, button: anchorBtn };
  }

  function buildLayoutPreviewHtml(variant) {
    var previewCount = getLayoutPreviewCount(variant);
    var visibleCells = Math.max(1, Math.min(previewCount, 4));
    var columns = visibleCells === 1 ? 1 : 2;
    var rows = visibleCells > 2 ? 2 : 1;
    var cells = [];

    for (var i = 0; i < visibleCells; i += 1) {
      var hasMore = i === visibleCells - 1 && previewCount > visibleCells;
      cells.push(
        '<span style="' + [
          'display:flex',
          'align-items:center',
          'justify-content:center',
          'min-width:0',
          'min-height:0',
          'border-radius:2px',
          'background:rgba(215,183,128,0.82)',
          'color:rgba(30,24,20,0.92)',
          'font-size:9px',
          'font-weight:700',
          'line-height:1'
        ].join(';') + '">' + (hasMore ? '+' : '') + '</span>'
      );
    }

    return '<span aria-hidden="true" style="' + [
      'display:block',
      'width:42px',
      'height:32px',
      'flex:0 0 42px',
      'box-sizing:border-box',
      'padding:4px',
      'border:1px solid rgba(255,255,255,0.18)',
      'border-radius:4px',
      'background:rgba(255,255,255,0.08)'
    ].join(';') + '">' +
      '<span style="' + [
        'display:grid',
        'grid-template-columns:repeat(' + columns + ',1fr)',
        'grid-template-rows:repeat(' + rows + ',1fr)',
        'gap:2px',
        'width:100%',
        'height:100%'
      ].join(';') + '">' + cells.join('') + '</span>' +
    '</span>';
  }

  function getLayoutPreviewCount(variant) {
    var imageCount = getVariantImageCount(variant);
    if (imageCount) return imageCount;

    var slots = getVariantSlots(variant);
    var imageSlots = countImageSlots(slots);
    if (imageSlots) return imageSlots;

    return Math.max(1, Math.min(slots.length || 1, 3));
  }

  function getVariantImageCount(variant) {
    var value = getVariantMetadataValue(variant, 'image_count', 'imageCount');
    if (value === undefined || value === null || value === '') return 0;

    var count = parseInt(value, 10);
    return isNaN(count) || count <= 0 ? 0 : count;
  }

  function getVariantSlots(variant) {
    var slots = getVariantMetadataValue(variant, 'slots', 'slots');
    var template = getVariantMetadataValue(variant, 'template', 'template');

    if (!slots && template && typeof template === 'object') {
      slots = template.slots || template.layout_slots || template.layoutSlots;
    }

    if (Array.isArray(slots)) return slots;
    if (slots && typeof slots === 'object') {
      return Object.keys(slots).map(function (key) {
        return slots[key];
      });
    }
    return [];
  }

  function countImageSlots(slots) {
    return slots.reduce(function (total, slot) {
      return total + (isImageSlot(slot) ? 1 : 0);
    }, 0);
  }

  function isImageSlot(slot) {
    var value = '';

    if (typeof slot === 'string') {
      value = slot;
    } else if (slot && typeof slot === 'object') {
      value = [
        slot.kind,
        slot.type,
        slot.slot_type,
        slot.slotType,
        slot.role,
        slot.name,
        slot.id
      ].join(' ');
    }

    value = value.toLowerCase();
    return value.indexOf('image') !== -1 ||
      value.indexOf('photo') !== -1 ||
      value.indexOf('picture') !== -1 ||
      value.indexOf('gallery') !== -1;
  }

  function getVariantMetadataValue(variant, snakeName, camelName) {
    var raw = variant && variant.raw ? variant.raw : {};
    if (variant && variant[snakeName] !== undefined && variant[snakeName] !== null) return variant[snakeName];
    if (variant && camelName && variant[camelName] !== undefined && variant[camelName] !== null) return variant[camelName];
    if (raw[snakeName] !== undefined && raw[snakeName] !== null) return raw[snakeName];
    if (camelName && raw[camelName] !== undefined && raw[camelName] !== null) return raw[camelName];
    return undefined;
  }

  function buildLayoutChipHtml(label, tone) {
    var isCurrent = tone === 'current';
    return '<span style="' + [
      'display:inline-flex',
      'align-items:center',
      'flex:0 0 auto',
      'border-radius:999px',
      'padding:2px 5px',
      'font-size:9px',
      'line-height:1',
      'letter-spacing:0.02em',
      'border:1px solid ' + (isCurrent ? 'rgba(255,255,255,0.28)' : 'rgba(215,183,128,0.38)'),
      'background:' + (isCurrent ? 'rgba(255,255,255,0.13)' : 'rgba(215,183,128,0.17)'),
      'color:' + (isCurrent ? 'rgba(255,255,255,0.86)' : 'rgba(255,236,198,0.95)')
    ].join(';') + '">' + escapeHtml(label) + '</span>';
  }

  function formatImageCount(count) {
    return count + ' image' + (count === 1 ? '' : 's');
  }

  function closeLayoutChooser() {
    if (activeChooser && activeChooser.panel && activeChooser.panel.parentNode) {
      activeChooser.panel.parentNode.removeChild(activeChooser.panel);
    }
    activeChooser = null;
  }

  function positionChooser(panel, anchorBtn) {
    var rect = anchorBtn.getBoundingClientRect();
    var top = rect.bottom + 8;
    var left = rect.left;
    panel.style.top = top + 'px';
    panel.style.left = left + 'px';

    var panelRect = panel.getBoundingClientRect();
    if (panelRect.right > window.innerWidth - 8) {
      panel.style.left = Math.max(8, window.innerWidth - panelRect.width - 8) + 'px';
    }
    if (panelRect.bottom > window.innerHeight - 8) {
      panel.style.top = Math.max(8, rect.top - panelRect.height - 8) + 'px';
    }
  }

  function swapSlideLayout(slide, variant, anchorBtn) {
    var endpoint = resolveRenderEndpoint(variant, slide);
    if (!endpoint) {
      showToast('Layout renderer is not available for this slide yet.');
      return;
    }

    closeLayoutChooser();

    var fromLayout = getCurrentLayoutId(slide);
    var toLayout = variant.id;
    var currentCapture = captureSlideContent(slide);
    var hidden = slide.classList.contains('slide-hidden');
    var instanceId = ensureSlideIdentity(slide);
    var slideType = getSlideType(slide);
    var slideIndex = getSlideIndex(slide);
    var renderContent = buildRenderContent(slide, slideType);
    var renderContext = buildRenderContext(slide);

    saveLayoutSnapshot(slide, fromLayout, currentCapture);
    setButtonBusy(anchorBtn, true);

    var payload = {
      project_id: window.__PROJECT_ID__ || null,
      slide_id: slide.id || '',
      slide_instance_id: instanceId,
      slide_num: slideIndex,
      slide_index: slideIndex,
      slide_type: slideType,
      type: slideType,
      from_layout_id: fromLayout,
      to_layout_id: toLayout,
      layout_id: toLayout,
      variant_id: toLayout,
      content: renderContent,
      context: renderContext,
      slots: currentCapture.slots,
      slot_order: currentCapture.orderByKind,
      overflow: getLayoutOverflowForSlide(slide),
      metadata: {
        label_text: (getSlideLabel(slide) || {}).textContent || '',
        variant: variant
      }
    };

    fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function (res) {
        var contentType = res.headers.get('content-type') || '';
        if (!res.ok) {
          return res.text().then(function (text) {
            throw new Error(extractErrorMessage(text) || ('Layout render failed (' + res.status + ')'));
          });
        }
        if (contentType.indexOf('application/json') !== -1) return res.json();
        return res.text();
      })
      .then(function (responsePayload) {
        var resolvedLayout = getResolvedLayoutId(responsePayload, toLayout);
        var replacement = replaceSlideWithResponse(slide, responsePayload, variant);
        var newSlide = replacement.slide;
        var newLabel = replacement.label;

        newSlide.setAttribute('data-slide-instance-id', instanceId);
        newSlide.setAttribute('data-slide-type', slideType);
        newSlide.setAttribute('data-layout-id', resolvedLayout);
        newSlide.setAttribute('data-layout-variant', resolvedLayout);
        if (resolvedLayout !== toLayout) {
          newSlide.setAttribute('data-requested-layout-id', toLayout);
        } else {
          newSlide.removeAttribute('data-requested-layout-id');
        }
        applyCurrentColourToSlide(newSlide);
        newSlide.classList.toggle('slide-hidden', hidden);
        if (newLabel) newLabel.classList.toggle('slide-hidden', hidden);
        setCurrentLayoutState(newSlide, toLayout, slideType);

        var targetSnapshot = getSavedLayoutSnapshot(newSlide, resolvedLayout) || getSavedLayoutSnapshot(newSlide, toLayout);

        prepareSlide(newSlide);
        var restoreResult = restoreContentIntoSlide(newSlide, currentCapture, targetSnapshot ? targetSnapshot.capture : null);
        applyCurrentColourToSlide(newSlide);

        var fromSnapshot = getSavedLayoutSnapshot(newSlide, fromLayout);
        if (fromSnapshot) fromSnapshot.overflowSlotIds = restoreResult.unusedCurrentSlotIds;

        renumberSlides();
        syncNavBar();
        hydrateDynamicSlideControls(newSlide);

        var finalCapture = captureSlideContent(newSlide);
        saveLayoutSnapshot(newSlide, resolvedLayout, finalCapture);
        if (resolvedLayout !== toLayout) saveLayoutSnapshot(newSlide, toLayout, finalCapture);
        setCurrentLayoutState(newSlide, toLayout, slideType);
        requestAutosave(true);
        if (window.pushUndoState) window.pushUndoState();

        document.dispatchEvent(new CustomEvent('brochure:layout-swapped', {
          detail: {
            slide: newSlide,
            slideId: newSlide.id,
            slideType: slideType,
            fromLayoutId: fromLayout,
            toLayoutId: resolvedLayout,
            requestedLayoutId: toLayout
          }
        }));
      })
      .catch(function (err) {
        showToast(err.message || 'Layout swap failed.');
      })
      .finally(function () {
        setButtonBusy(anchorBtn, false);
      });
  }

  function replaceSlideWithResponse(slide, responsePayload, variant) {
    var parsed = normalizeRenderResponse(responsePayload);
    var html = parsed.html || '';
    var oldLabel = getSlideLabel(slide);
    var temp = document.createElement('div');

    if (parsed.labelHtml || parsed.slideHtml) {
      temp.innerHTML = (parsed.labelHtml || '') + (parsed.slideHtml || '');
    } else {
      temp.innerHTML = html;
    }

    var newLabel = temp.querySelector('.slide-label');
    var newSlide = temp.querySelector('.slide-wrapper');

    if (!newSlide && temp.firstElementChild && temp.firstElementChild.classList.contains('slide-wrapper')) {
      newSlide = temp.firstElementChild;
    }
    if (!newSlide) throw new Error('Layout renderer did not return a .slide-wrapper.');

    if (!newLabel) {
      if (oldLabel) {
        newLabel = oldLabel.cloneNode(true);
      } else {
        newLabel = document.createElement('div');
        newLabel.className = 'slide-label';
      }
      if (parsed.labelText) newLabel.textContent = parsed.labelText;
      else if (variant && variant.label) newLabel.textContent = 'SLIDE ' + getSlideIndex(slide) + ' - ' + String(variant.label).toUpperCase();
    }

    stripEditorArtifacts(newSlide);

    var parent = slide.parentNode;
    if (oldLabel) {
      parent.replaceChild(newLabel, oldLabel);
    } else {
      parent.insertBefore(newLabel, slide);
    }
    parent.replaceChild(newSlide, slide);

    return { label: newLabel, slide: newSlide };
  }

  function normalizeRenderResponse(payload) {
    if (typeof payload === 'string') {
      return { html: payload };
    }
    payload = payload || {};
    return {
      labelHtml: payload.label_html || payload.labelHtml || '',
      slideHtml: payload.slide_html || payload.slideHtml || '',
      html: payload.html || payload.rendered_html || payload.renderedHtml || '',
      labelText: payload.label_text || payload.labelText || ''
    };
  }

  function getResolvedLayoutId(payload, fallbackLayoutId) {
    if (payload && typeof payload === 'object') {
      return payload.layout_id || payload.layoutId || (payload.layout && payload.layout.id) || fallbackLayoutId;
    }
    return fallbackLayoutId;
  }

  function buildRenderContext(slide) {
    var titleParts = String(document.title || '').split(/[—-]/);
    var brochureName = slotText(document, 'heading') || titleParts[0] || 'Building Name';
    var location = titleParts.length > 1 ? titleParts.slice(1).join('-').trim() : 'Location';
    var headerNameEl = document.querySelector('[data-slot-id="header-name"], .bldg-name');
    var subtitleEl = document.querySelector('#coverSubtitle, .slide-cover .subtitle');

    return {
      brochure_name: cleanText(brochureName),
      location: cleanText(location || 'Location'),
      header_name: cleanText(headerNameEl ? headerNameEl.textContent : ''),
      subtitle: cleanText(subtitleEl ? subtitleEl.textContent : '')
    };
  }

  function buildRenderContent(slide, slideType) {
    if (slideType === 'cover') {
      return {
        heading: slotText(slide, 'heading'),
        subheading: slotText(slide, 'subheading'),
        tagline: slotText(slide, 'tagline')
      };
    }

    if (slideType === 'text_and_photos') {
      return {
        heading: slotText(slide, 'heading'),
        body_text: slotText(slide, 'body'),
        images: collectImageContent(slide, 4)
      };
    }

    if (slideType === 'highlights_grid') {
      return {
        heading: slotText(slide, 'heading'),
        features: Array.from(slide.querySelectorAll('.highlight-item')).map(function (item, index) {
          var icon = item.querySelector('[data-icon-id], .highlight-icon');
          return {
            text: slotText(item, 'feature-' + (index + 1) + '-text') || cleanText(item.textContent),
            icon_hint: icon ? (icon.getAttribute('data-icon-id') || 'warehouse') : 'warehouse'
          };
        })
      };
    }

    if (slideType === 'photo_gallery') {
      return { images: collectImageContent(slide, 4) };
    }

    if (slideType === 'floor_plan') {
      return {
        heading: slotText(slide, 'heading'),
        size_label: slotText(slide, 'size-label'),
        specs: Array.from(slide.querySelectorAll('[data-slot-id^="spec-"][contenteditable="true"], .spec-list li')).map(function (el) {
          return cleanText(el.textContent);
        }).filter(Boolean),
        note: slotText(slide, 'note'),
        legend: Array.from(slide.querySelectorAll('.legend-item')).map(function (item, index) {
          return {
            label: slotText(item, 'legend-' + (index + 1) + '-label') || cleanText(item.textContent),
            colour: 'blue'
          };
        })
      };
    }

    if (slideType === 'services_grid') {
      return {
        heading: slotText(slide, 'heading'),
        intro_text: slotText(slide, 'intro'),
        services: Array.from(slide.querySelectorAll('.managed-item')).map(function (item, index) {
          var icon = item.querySelector('[data-icon-id], .managed-icon');
          return {
            label: slotText(item, 'service-' + (index + 1) + '-label') || cleanText(item.textContent),
            icon_hint: icon ? (icon.getAttribute('data-icon-id') || 'key') : 'key'
          };
        }),
        note: slotText(slide, 'note'),
        images: collectImageContent(slide, 4)
      };
    }

    if (slideType === 'location') {
      return {
        heading: slotText(slide, 'heading'),
        body_text: slotText(slide, 'body'),
        images: collectImageContent(slide, 6)
      };
    }

    if (slideType === 'travel_map') {
      return {
        heading: slotText(slide, 'heading'),
        stations: Array.from(slide.querySelectorAll('[data-station], .travel-route')).map(function (row) {
          var nameText = cleanText((row.querySelector('.route-name') || row).textContent);
          return {
            name: nameText,
            time: '',
            lines: Array.from(row.querySelectorAll('.t-dot[data-line]')).map(function (dot) {
              return dot.getAttribute('data-line');
            }).filter(Boolean)
          };
        }),
        note: slotText(slide, 'note')
      };
    }

    if (slideType === 'contacts') {
      return {
        heading: slotText(slide, 'heading'),
        agency: slotText(slide, 'agency-name'),
        contacts: Array.from(slide.querySelectorAll('.contact-card')).map(function (card, index) {
          return {
            name: slotText(card, 'contact-' + (index + 1) + '-name'),
            email: slotText(card, 'contact-' + (index + 1) + '-email'),
            phone: slotText(card, 'contact-' + (index + 1) + '-phone')
          };
        }),
        legal_text: slotText(slide, 'legal-text'),
        marketing_credit: slotText(slide, 'marketing-credit')
      };
    }

    return { heading: slotText(slide, 'heading') };
  }

  function collectImageContent(slide, count) {
    var images = [];
    for (var i = 1; i <= count; i++) {
      var imageEl = slide.querySelector('[data-slot-id="image-' + i + '"]');
      var caption = slotText(slide, 'image-' + i + '-caption');
      var labelEl = imageEl ? imageEl.querySelector('.ph-label') : null;
      images.push({
        description: cleanText(labelEl ? labelEl.textContent : ('Photo ' + i)),
        caption: caption
      });
    }
    return images;
  }

  function slotText(root, slotId) {
    var el = root.querySelector ? root.querySelector('[data-slot-id="' + cssEscape(slotId) + '"]') : null;
    return el ? cleanText(el.textContent) : '';
  }

  function cleanText(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }

  function cssEscape(value) {
    if (window.CSS && window.CSS.escape) return window.CSS.escape(value);
    return String(value).replace(/["\\]/g, '\\$&');
  }

  function restoreSavedLayoutsFromState() {
    if (!layoutState || !layoutState.slides) return;

    document.querySelectorAll('.slide-wrapper').forEach(function (slide) {
      prepareSlide(slide);
      var record = getLayoutStateRecord(slide, false);
      if (!record || !record.currentLayout || !record.layouts) return;
      var currentLayout = getCurrentLayoutId(slide);
      var targetSnapshot = record.layouts[record.currentLayout];
      if (!targetSnapshot || !targetSnapshot.slideHtml || record.currentLayout === currentLayout) return;

      var currentCapture = captureSlideContent(slide);
      var replacement = replaceSlideWithResponse(slide, {
        label_html: targetSnapshot.labelHtml || '',
        slide_html: targetSnapshot.slideHtml
      }, { id: record.currentLayout, label: record.currentLayout });

      var restoredSlide = replacement.slide;
      restoredSlide.setAttribute('data-slide-instance-id', record.slideInstanceId || ensureSlideIdentity(restoredSlide));
      restoredSlide.setAttribute('data-layout-id', record.currentLayout);
      if (record.slideType) restoredSlide.setAttribute('data-slide-type', record.slideType);
      prepareSlide(restoredSlide);
      restoreContentIntoSlide(restoredSlide, currentCapture, targetSnapshot.capture || null);
      hydrateDynamicSlideControls(restoredSlide);
      applyCurrentColourToSlide(restoredSlide);
    });

    renumberSlides();
    syncNavBar();
  }

  function captureSlideContent(slide) {
    ensureRuntimeSlotIds(slide);

    var capture = {
      version: 1,
      slideType: getSlideType(slide),
      layoutId: getCurrentLayoutId(slide),
      labelHtml: cleanLabelHtml(getSlideLabel(slide)),
      slideHtml: cleanSlideHtml(slide),
      slots: {},
      orderByKind: {}
    };

    slide.querySelectorAll('[data-slot-id]').forEach(function (el) {
      var slotId = el.getAttribute('data-slot-id');
      if (!slotId) return;
      var entry = captureSlot(el, slotId);
      if (entry.kind === 'structure') return;
      capture.slots[slotId] = entry;
      if (!capture.orderByKind[entry.kind]) capture.orderByKind[entry.kind] = [];
      capture.orderByKind[entry.kind].push(slotId);
    });

    return capture;
  }

  function captureSlot(el, slotId) {
    var kind = getSlotKind(el);
    var entry = {
      slotId: slotId,
      kind: kind,
      className: el.className || '',
      attrs: captureDataAttributes(el),
      styles: {}
    };

    if (kind === 'text') {
      entry.html = el.innerHTML;
      entry.text = el.textContent;
    } else if (kind === 'image') {
      entry.styles = captureImageStyles(el);
      entry.hasImage = !!(entry.styles.backgroundImage && entry.styles.backgroundImage !== 'none');
    } else if (kind === 'icon') {
      entry.html = el.innerHTML;
      entry.iconId = el.getAttribute('data-icon-id') || '';
    } else if (kind === 'map') {
      entry.html = el.innerHTML;
      entry.styles = captureImageStyles(el);
      entry.mapLoaded = el.classList.contains('map-loaded') || !!el.querySelector('svg');
    } else if (kind === 'logo') {
      entry.html = el.innerHTML;
      entry.inlineStyle = el.getAttribute('style') || '';
    } else {
      entry.html = el.innerHTML;
    }

    return entry;
  }

  function restoreContentIntoSlide(slide, currentCapture, targetCapture) {
    ensureRuntimeSlotIds(slide);

    var usedCurrent = {};
    var usedTarget = {};

    slide.querySelectorAll('[data-slot-id]').forEach(function (el) {
      var kind = getSlotKind(el);
      var slotId = el.getAttribute('data-slot-id');
      var choice = chooseSlotContent(slotId, kind, currentCapture, targetCapture, usedCurrent, usedTarget);
      if (!choice || !choice.entry) return;

      if (applySlot(el, choice.entry, kind)) {
        if (choice.source === 'current') usedCurrent[choice.entry.slotId] = true;
        if (choice.source === 'target') usedTarget[choice.entry.slotId] = true;
      }
    });

    var unused = [];
    if (currentCapture && currentCapture.slots) {
      Object.keys(currentCapture.slots).forEach(function (slotId) {
        if (!usedCurrent[slotId]) unused.push(slotId);
      });
    }

    return { unusedCurrentSlotIds: unused };
  }

  function chooseSlotContent(slotId, kind, currentCapture, targetCapture, usedCurrent, usedTarget) {
    var current = currentCapture && currentCapture.slots ? currentCapture.slots : {};
    var target = targetCapture && targetCapture.slots ? targetCapture.slots : {};

    if (current[slotId] && isCompatibleSlot(current[slotId], kind)) {
      return { source: 'current', entry: current[slotId] };
    }
    if (target[slotId] && isCompatibleSlot(target[slotId], kind)) {
      return { source: 'target', entry: target[slotId] };
    }

    var orderedCurrent = takeFirstCompatibleByKind(currentCapture, kind, usedCurrent);
    if (orderedCurrent) return { source: 'current', entry: orderedCurrent };

    var orderedTarget = takeFirstCompatibleByKind(targetCapture, kind, usedTarget);
    if (orderedTarget) return { source: 'target', entry: orderedTarget };

    return null;
  }

  function takeFirstCompatibleByKind(capture, kind, used) {
    if (!capture || !capture.orderByKind || !capture.slots) return null;
    var order = capture.orderByKind[kind] || [];
    for (var i = 0; i < order.length; i++) {
      var slotId = order[i];
      var entry = capture.slots[slotId];
      if (!used[slotId] && isCompatibleSlot(entry, kind)) return entry;
    }
    return null;
  }

  function isCompatibleSlot(entry, targetKind) {
    if (!entry) return false;
    if (entry.kind === targetKind) return true;
    return targetKind === 'image' && entry.kind === 'map';
  }

  function applySlot(el, entry, targetKind) {
    if (targetKind === 'text' && typeof entry.html === 'string') {
      el.innerHTML = entry.html;
      return true;
    }

    if (targetKind === 'image') {
      applyImageSlot(el, entry);
      return true;
    }

    if (targetKind === 'icon' && typeof entry.html === 'string') {
      el.innerHTML = entry.html;
      if (entry.iconId) el.setAttribute('data-icon-id', entry.iconId);
      return true;
    }

    if (targetKind === 'map') {
      applyMapSlot(el, entry);
      return true;
    }

    if (targetKind === 'logo' && typeof entry.html === 'string') {
      el.innerHTML = entry.html;
      if (entry.inlineStyle) el.setAttribute('style', entry.inlineStyle);
      return true;
    }

    if (typeof entry.html === 'string' && targetKind === entry.kind) {
      el.innerHTML = entry.html;
      return true;
    }

    return false;
  }

  function applyImageSlot(el, entry) {
    var styles = entry.styles || {};
    if (styles.backgroundImage) el.style.backgroundImage = styles.backgroundImage;
    if (styles.backgroundPosition) el.style.backgroundPosition = styles.backgroundPosition;
    if (styles.backgroundSize) el.style.backgroundSize = styles.backgroundSize;
    if (styles.backgroundRepeat) el.style.backgroundRepeat = styles.backgroundRepeat;
    if (styles.border) el.style.border = styles.border;
    if (styles.cursor) el.style.cursor = styles.cursor;

    applyDataAttributes(el, entry.attrs);

    if (styles.backgroundImage && styles.backgroundImage !== 'none') {
      el.querySelectorAll('.ph-icon, .ph-label').forEach(function (node) {
        node.style.display = 'none';
      });
      el.style.border = styles.border || 'none';
      el.style.cursor = styles.cursor || 'default';
    }
  }

  function applyMapSlot(el, entry) {
    applyImageSlot(el, entry);
    applyDataAttributes(el, entry.attrs);

    if (entry.html && (entry.mapLoaded || entry.html.trim().toLowerCase().indexOf('<svg') === 0)) {
      el.innerHTML = entry.html;
      el.classList.remove('img-placeholder');
      el.classList.add('map-loaded');
      el.style.backgroundImage = 'none';
      el.style.background = 'transparent';
      el.style.border = 'none';
    }
  }

  function captureImageStyles(el) {
    return {
      backgroundImage: el.style.backgroundImage || '',
      backgroundPosition: el.style.backgroundPosition || '',
      backgroundSize: el.style.backgroundSize || '',
      backgroundRepeat: el.style.backgroundRepeat || '',
      border: el.style.border || '',
      cursor: el.style.cursor || ''
    };
  }

  function captureDataAttributes(el) {
    var attrs = {};
    Array.from(el.attributes || []).forEach(function (attr) {
      if (attr.name.indexOf('data-') === 0) attrs[attr.name] = attr.value;
    });
    return attrs;
  }

  function applyDataAttributes(el, attrs) {
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (name) {
      if (name === 'data-slot-id' || name === 'data-save-id') return;
      el.setAttribute(name, attrs[name]);
    });
  }

  function ensureRuntimeSlotIds(slide) {
    assignSlotIds(slide, '.map-area', 'map');
    assignSlotIds(slide, '.img-placeholder:not(.map-area)', 'image');
    assignSlotIds(slide, '.highlight-icon, .managed-icon, [data-icon-id]', 'icon');
    assignSlotIds(slide, '.logo-zone', 'logo');
    assignSlotIds(slide, '[contenteditable="true"]', 'text');
  }

  function assignSlotIds(slide, selector, prefix) {
    Array.from(slide.querySelectorAll(selector)).forEach(function (el, index) {
      if (!el.getAttribute('data-slot-id')) {
        el.setAttribute('data-slot-id', prefix + '-' + (index + 1));
      }
    });
  }

  function getSlotKind(el) {
    if (el.classList.contains('map-area')) return 'map';
    if (el.classList.contains('img-placeholder') || (el.getAttribute('data-save-id') || '').indexOf('__ph__') !== -1) return 'image';
    if (el.classList.contains('highlight-icon') || el.classList.contains('managed-icon') || el.hasAttribute('data-icon-id')) return 'icon';
    if (el.classList.contains('logo-zone')) return 'logo';
    if (el.getAttribute('contenteditable') === 'true') return 'text';
    if (el.querySelector('[data-slot-id]')) return 'structure';
    if (el.hasAttribute('data-slot-id') && cleanText(el.textContent)) return 'text';
    return 'html';
  }

  function getLayoutMetadata() {
    if (layoutMeta) return layoutMeta;

    layoutMeta = { renderEndpoint: '', variantsByType: {} };
    [
      window.__BROCHURE_LAYOUTS__,
      window.__BROCHURE_LAYOUT_VARIANTS__,
      window.__LAYOUT_VARIANTS__,
      window.__layoutVariants
    ].forEach(ingestLayoutMetadata);

    ['layoutVariants', 'layout-variants', 'brochureLayoutVariants', 'brochure-layout-variants'].forEach(function (id) {
      var script = document.getElementById(id);
      if (!script || !script.textContent) return;
      try {
        ingestLayoutMetadata(JSON.parse(script.textContent));
      } catch (_) {}
    });

    var body = document.body;
    if (body) {
      if (body.getAttribute('data-layout-render-endpoint')) {
        layoutMeta.renderEndpoint = body.getAttribute('data-layout-render-endpoint');
      }
      if (body.getAttribute('data-layout-variants')) {
        try {
          ingestLayoutMetadata(JSON.parse(body.getAttribute('data-layout-variants')));
        } catch (_) {}
      }
    }

    return layoutMeta;
  }

  function ingestLayoutMetadata(raw) {
    if (!raw) return;

    if (Array.isArray(raw)) {
      raw.forEach(function (entry) {
        if (entry && Array.isArray(entry.variants)) {
          entry.variants.forEach(function (variant) {
            addLayoutVariant(entry.slide_type || entry.slideType || entry.type, variant);
          });
        } else {
          addLayoutVariant(entry.slide_type || entry.slideType || entry.type, entry);
        }
      });
      return;
    }

    layoutMeta.renderEndpoint =
      raw.render_endpoint ||
      raw.renderEndpoint ||
      raw.endpoint ||
      (raw.endpoints && (raw.endpoints.render || raw.endpoints.layout_render)) ||
      layoutMeta.renderEndpoint ||
      '';

    ingestVariantCollection(raw.variants);
    ingestVariantCollection(raw.layouts);
    ingestVariantCollection(raw.slide_types || raw.slideTypes);
    ingestVariantCollection(raw.slides);
  }

  function ingestVariantCollection(collection) {
    if (!collection) return;

    if (Array.isArray(collection)) {
      collection.forEach(function (entry) {
        if (entry && Array.isArray(entry.variants)) {
          entry.variants.forEach(function (variant) {
            addLayoutVariant(entry.slide_type || entry.slideType || entry.type, variant);
          });
        } else {
          addLayoutVariant(entry.slide_type || entry.slideType || entry.type, entry);
        }
      });
      return;
    }

    Object.keys(collection).forEach(function (type) {
      var entry = collection[type];
      var variants = Array.isArray(entry) ? entry : (entry && (entry.variants || entry.layouts));
      if (!variants && entry && (entry.id || entry.layout_id || entry.layoutId || entry.variant_id || entry.variantId || entry.name)) {
        variants = [entry];
      }
      variants = variants || [];
      variants.forEach(function (variant) {
        addLayoutVariant(type, variant);
      });
    });
  }

  function addLayoutVariant(type, variant) {
    if (!variant) return;
    type = normalizeType(type || variant.slide_type || variant.slideType || variant.type || 'default');
    var id = variant.id || variant.layout_id || variant.layoutId || variant.variant_id || variant.variantId || variant.name;
    if (!id) return;

    if (!layoutMeta.variantsByType[type]) layoutMeta.variantsByType[type] = [];
    layoutMeta.variantsByType[type].push({
      id: String(id),
      label: variant.label || variant.title || variant.name || String(id),
      description: variant.description || variant.summary || '',
      renderEndpoint: variant.render_endpoint || variant.renderEndpoint || variant.endpoint || '',
      raw: variant
    });
  }

  function getVariantsForSlide(slide) {
    var inline = slide.getAttribute('data-layout-variants');
    if (inline) {
      try {
        var parsed = JSON.parse(inline);
        if (Array.isArray(parsed)) return parsed.map(normalizeInlineVariant).filter(Boolean);
      } catch (_) {}
    }

    var meta = getLayoutMetadata();
    var type = getSlideType(slide);
    return []
      .concat(meta.variantsByType[type] || [])
      .concat(meta.variantsByType.default || [])
      .concat(meta.variantsByType.all || []);
  }

  function normalizeInlineVariant(variant) {
    if (!variant) return null;
    var id = variant.id || variant.layout_id || variant.layoutId || variant.variant_id || variant.variantId || variant.name;
    if (!id) return null;
    return {
      id: String(id),
      label: variant.label || variant.title || variant.name || String(id),
      description: variant.description || '',
      renderEndpoint: variant.render_endpoint || variant.renderEndpoint || variant.endpoint || '',
      raw: variant
    };
  }

  function resolveRenderEndpoint(variant, slide) {
    var endpoint =
      (variant && variant.renderEndpoint) ||
      slide.getAttribute('data-layout-render-endpoint') ||
      getLayoutMetadata().renderEndpoint ||
      '';

    if (!endpoint) endpoint = '/api/slides/render';

    if (!endpoint) return '';
    return endpoint
      .replace('{project_id}', encodeURIComponent(window.__PROJECT_ID__ || ''))
      .replace(':project_id', encodeURIComponent(window.__PROJECT_ID__ || ''));
  }

  function getSlideType(slide) {
    var explicit = slide.getAttribute('data-slide-type') || slide.getAttribute('data-layout-type') || slide.getAttribute('data-template-type');
    if (explicit) return normalizeType(explicit);

    var classMap = {
      'slide-cover': 'cover',
      'slide-text-photos': 'text_and_photos',
      'slide-highlights': 'highlights_grid',
      'slide-gallery': 'photo_gallery',
      'slide-floorplan': 'floor_plan',
      'slide-services': 'services_grid',
      'slide-location': 'location',
      'slide-travel': 'travel_map',
      'slide-contacts': 'contacts'
    };
    var found = Object.keys(classMap).find(function (className) {
      return slide.classList.contains(className);
    });
    return found ? classMap[found] : 'default';
  }

  function normalizeType(type) {
    return String(type || 'default').trim().toLowerCase().replace(/[-\s]+/g, '_');
  }

  function getCurrentLayoutId(slide) {
    return slide.getAttribute('data-layout-id') ||
      slide.getAttribute('data-layout-variant') ||
      slide.getAttribute('data-layout') ||
      getSlideType(slide);
  }

  function syncLayoutState() {
    var externalState = normalizeLayoutState(window.__brochureLayoutState);
    var currentState = normalizeLayoutState(layoutState);

    if (externalState !== currentState) {
      mergeLayoutSlides(externalState, currentState);
    }

    layoutState = externalState;
    window.__brochureLayoutState = layoutState;
    return layoutState;
  }

  function normalizeLayoutState(state) {
    if (!state || typeof state !== 'object' || Array.isArray(state)) {
      state = { version: LAYOUT_STATE_VERSION, slides: {} };
    }
    if (!state.version) state.version = LAYOUT_STATE_VERSION;
    if (!state.slides || typeof state.slides !== 'object' || Array.isArray(state.slides)) {
      state.slides = {};
    }
    return state;
  }

  function mergeLayoutSlides(targetState, sourceState) {
    var targetSlides = targetState.slides || {};
    var sourceSlides = sourceState.slides || {};

    Object.keys(sourceSlides).forEach(function (key) {
      var sourceRecord = sourceSlides[key];
      var targetRecord = targetSlides[key];
      if (!targetRecord || ((sourceRecord && sourceRecord.updatedAt) || 0) > ((targetRecord && targetRecord.updatedAt) || 0)) {
        targetSlides[key] = sourceRecord;
      }
    });
  }

  function ensureSlideIdentity(slide) {
    var existing = slide.getAttribute('data-slide-instance-id');
    if (existing) return existing;
    var id = slide.id || ('slide-' + Math.random().toString(36).slice(2, 10));
    slide.setAttribute('data-slide-instance-id', id);
    return id;
  }

  function getSlideIndex(slide) {
    return Array.from(document.querySelectorAll('.slide-wrapper')).indexOf(slide) + 1;
  }

  function getSlideLabel(slide) {
    var prev = slide.previousElementSibling;
    return prev && prev.classList.contains('slide-label') ? prev : null;
  }

  function getLayoutStateRecord(slide, create) {
    syncLayoutState();
    var key = ensureSlideIdentity(slide);
    layoutState.slides = layoutState.slides || {};
    var record = layoutState.slides[key];

    if (!record && slide.id && layoutState.slides[slide.id]) {
      record = layoutState.slides[slide.id];
      layoutState.slides[key] = record;
    }

    if (!record) {
      Object.keys(layoutState.slides).some(function (candidateKey) {
        var candidate = layoutState.slides[candidateKey];
        if (candidate && candidate.slideInstanceId === key) {
          record = candidate;
          layoutState.slides[key] = record;
          return true;
        }
        return false;
      });
    }

    if (!record && create) {
      record = {
        slideInstanceId: key,
        slideId: slide.id || '',
        slideType: getSlideType(slide),
        currentLayout: getCurrentLayoutId(slide),
        layouts: {}
      };
      layoutState.slides[key] = record;
    }

    if (record) {
      record.slideInstanceId = key;
      record.slideId = slide.id || record.slideId || '';
      record.slideType = record.slideType || getSlideType(slide);
      layoutState.slides[key] = record;
      window.__brochureLayoutState = layoutState;
    }

    return record || null;
  }

  function saveLayoutSnapshot(slide, layoutId, capture) {
    var record = getLayoutStateRecord(slide, true);
    record.slideInstanceId = ensureSlideIdentity(slide);
    record.slideId = slide.id || record.slideId || '';
    record.slideType = getSlideType(slide);
    record.layouts = record.layouts || {};
    record.layouts[layoutId] = record.layouts[layoutId] || {};
    record.layouts[layoutId].capture = capture;
    record.layouts[layoutId].labelHtml = capture.labelHtml || '';
    record.layouts[layoutId].slideHtml = capture.slideHtml || '';
    record.layouts[layoutId].savedAt = Date.now();
    record.updatedAt = Date.now();
    window.__brochureLayoutState = layoutState;
    return record.layouts[layoutId];
  }

  function getSavedLayoutSnapshot(slide, layoutId) {
    var record = getLayoutStateRecord(slide, false);
    return record && record.layouts ? record.layouts[layoutId] : null;
  }

  function setCurrentLayoutState(slide, layoutId, slideType) {
    var record = getLayoutStateRecord(slide, true);
    record.slideInstanceId = ensureSlideIdentity(slide);
    record.slideId = slide.id || record.slideId || '';
    record.currentLayout = layoutId;
    record.slideType = slideType || getSlideType(slide);
    record.updatedAt = Date.now();
    window.__brochureLayoutState = layoutState;
  }

  function getLayoutOverflowForSlide(slide) {
    var record = getLayoutStateRecord(slide, false);
    return record && record.layouts ? record.layouts : {};
  }

  function cleanSlideHtml(slide) {
    if (!slide) return '';
    var clone = slide.cloneNode(true);
    stripEditorArtifacts(clone);
    return clone.outerHTML;
  }

  function cleanLabelHtml(label) {
    return label ? label.outerHTML : '';
  }

  function stripEditorArtifacts(root) {
    root.querySelectorAll('.slide-manager-bar, .layout-chooser, [data-layout-editor-only]').forEach(function (el) {
      el.remove();
    });
  }

  function hydrateDynamicSlideControls(slide) {
    slide.querySelectorAll('.img-placeholder').forEach(function (placeholder) {
      if (placeholder.getAttribute('data-layout-upload-bound') === 'true') return;
      placeholder.setAttribute('data-layout-upload-bound', 'true');
      placeholder.addEventListener('dragover', function (e) {
        e.preventDefault();
        placeholder.style.background = 'rgba(184,113,78,0.25)';
        placeholder.style.borderColor = 'rgba(255,255,255,0.6)';
      });
      placeholder.addEventListener('dragleave', function () {
        placeholder.style.background = '';
        placeholder.style.borderColor = '';
      });
      placeholder.addEventListener('drop', function (e) {
        e.preventDefault();
        placeholder.style.background = '';
        placeholder.style.borderColor = '';
        var file = e.dataTransfer.files[0];
        if (file && file.type && file.type.indexOf('image/') === 0) applyLocalImage(placeholder, file);
      });
      placeholder.addEventListener('click', function () {
        var input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.onchange = function (e) {
          var file = e.target.files[0];
          if (file) applyLocalImage(placeholder, file);
        };
        input.click();
      });
    });

    var addStationBtn = slide.querySelector('#addStationBtn');
    if (addStationBtn && window.buildStationRow && addStationBtn.getAttribute('data-layout-station-bound') !== 'true') {
      addStationBtn.setAttribute('data-layout-station-bound', 'true');
      addStationBtn.addEventListener('click', function () {
        var list = slide.querySelector('#stationList');
        if (!list) return;
        var row = window.buildStationRow();
        list.appendChild(row);
        var name = row.querySelector('.route-name');
        if (name) name.focus();
      });
    }

    slide.querySelectorAll('.route-delete-btn').forEach(function (btn) {
      if (btn.getAttribute('data-layout-delete-bound') === 'true') return;
      btn.setAttribute('data-layout-delete-bound', 'true');
      btn.addEventListener('click', function () {
        var row = btn.closest('.travel-route');
        if (row) row.remove();
      });
    });
  }

  function applyLocalImage(placeholder, file) {
    var reader = new FileReader();
    reader.onload = function (ev) {
      placeholder.querySelectorAll('.ph-icon, .ph-label').forEach(function (el) { el.style.display = 'none'; });
      placeholder.style.backgroundImage = 'url(' + ev.target.result + ')';
      placeholder.style.backgroundSize = 'cover';
      placeholder.style.backgroundPosition = 'center';
      placeholder.style.border = 'none';
      placeholder.style.cursor = 'default';
      requestAutosave();
      if (window.pushUndoState) window.pushUndoState();
    };
    reader.readAsDataURL(file);
  }

  function renumberSlides() {
    var slides = document.querySelectorAll('.slide-wrapper');
    slides.forEach(function (slide, idx) {
      slide.id = 'slide' + (idx + 1);
      var label = getSlideLabel(slide);
      if (label) {
        var text = label.textContent.replace(/SLIDE\s*\d+/i, 'SLIDE ' + (idx + 1));
        label.textContent = text;
      }
    });
  }

  function syncNavBar() {
    var panel = document.getElementById('slidesDropdownPanel');
    if (!panel) return;

    panel.innerHTML = '';

    var slides = document.querySelectorAll('.slide-wrapper');
    slides.forEach(function (slide, idx) {
      var isHidden = slide.classList.contains('slide-hidden');
      var label = getSlideLabel(slide);
      var labelText = '';
      if (label) {
        labelText = label.textContent.replace(/^SLIDE\s*\d+\s*[—–-]\s*/i, '').trim();
      }
      if (!labelText) labelText = 'Slide ' + (idx + 1);

      var item = document.createElement('div');
      item.className = 'slides-dropdown-item' + (isHidden ? ' hidden-slide' : '');
      item.setAttribute('data-slide-index', idx + 1);

      item.innerHTML =
        '<label class="slides-dropdown-toggle">' +
          '<input type="checkbox"' + (isHidden ? '' : ' checked') + ' data-slide-target="slide' + (idx + 1) + '">' +
        '</label>' +
        '<a href="#slide' + (idx + 1) + '" class="slides-dropdown-link">' + escapeHtml(labelText) + '</a>';

      panel.appendChild(item);
    });
  }

  var dropdownBtn = document.getElementById('slidesDropdownBtn');
  var dropdownPanel = document.getElementById('slidesDropdownPanel');
  if (dropdownBtn && dropdownPanel) {
    dropdownBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      dropdownPanel.classList.toggle('open');
    });

    document.addEventListener('click', function (e) {
      if (!dropdownPanel.contains(e.target) && e.target !== dropdownBtn) {
        dropdownPanel.classList.remove('open');
      }
    });

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
        var isHidden = slide.classList.toggle('slide-hidden');
        var label = getSlideLabel(slide);
        if (label) label.classList.toggle('slide-hidden', isHidden);
        requestAutosave();
        if (window.pushUndoState) window.pushUndoState();
      }
    });

    dropdownPanel.addEventListener('click', function (e) {
      var link = e.target.closest('.slides-dropdown-link');
      if (link) dropdownPanel.classList.remove('open');
    });
  }

  function requestAutosave(immediate) {
    if (window.requestBrochureAutoSave) {
      window.requestBrochureAutoSave(!!immediate);
    }
  }

  function applyCurrentColourToSlide(slide) {
    if (!slide || !window.applyBrochureColourToSlide) return;
    var colour = window.getBrochurePrimaryColour ? window.getBrochurePrimaryColour() : '';
    window.applyBrochureColourToSlide(slide, colour);
  }

  function setButtonBusy(btn, busy) {
    if (!btn) return;
    if (busy) {
      btn.setAttribute('data-layout-original-text', btn.textContent);
      btn.disabled = true;
      btn.textContent = '...';
    } else {
      btn.disabled = false;
      var original = btn.getAttribute('data-layout-original-text');
      if (original) btn.textContent = original;
      btn.removeAttribute('data-layout-original-text');
    }
  }

  function showToast(message) {
    if (window.showBrochureToast) {
      window.showBrochureToast(message);
      return;
    }
    window.alert(message);
  }

  function extractErrorMessage(text) {
    if (!text) return '';
    try {
      var parsed = JSON.parse(text);
      return parsed.detail || parsed.message || parsed.error || '';
    } catch (_) {
      return text.slice(0, 180);
    }
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  window.__brochureLayoutManager = {
    captureSlideContent: captureSlideContent,
    restoreContentIntoSlide: restoreContentIntoSlide,
    getLayoutMetadata: getLayoutMetadata,
    getVariantsForSlide: getVariantsForSlide,
    prepareSlide: prepareSlide
  };
});
