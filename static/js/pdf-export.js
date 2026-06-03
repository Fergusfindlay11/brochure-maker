/**
 * PDF Export, Save Template, and Download HTML handlers.
 */
document.addEventListener('DOMContentLoaded', function () {
  var pdfOverlay = document.getElementById('pdfOverlay');
  var exportBtn  = document.getElementById('exportPdfBtn');

  // ── Export PDF (print) ──
  exportBtn.addEventListener('click', function () {
    pdfOverlay.classList.add('show');
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        setTimeout(function () {
          pdfOverlay.classList.remove('show');
          window.print();
        }, 600);
      });
    });
  });

  window.addEventListener('afterprint', function () {
    pdfOverlay.classList.remove('show');
  });

  // ── Export HQ PDF (Server-Side Playwright) ──
  var hqPdfBtn = document.getElementById('exportHqPdfBtn');
  if (hqPdfBtn) {
    hqPdfBtn.addEventListener('click', function () {
      var projectId = window.__PROJECT_ID__;
      if (!projectId) {
        showBrochureToast('Cannot export — no project ID found.');
        return;
      }

      hqPdfBtn.disabled = true;
      hqPdfBtn.textContent = 'Rendering...';
      showBrochureToast('Generating high-quality PDF on server...');

      fetch('/api/projects/' + projectId + '/export/pdf', { method: 'POST' })
        .then(function (res) {
          if (res.status === 501) {
            throw new Error('Server-side PDF not available. Playwright not installed.');
          }
          if (!res.ok) throw new Error('PDF export failed');
          return res.blob();
        })
        .then(function (blob) {
          var title = document.title.replace(/[^a-zA-Z0-9 \-_]/g, '').trim().replace(/\s+/g, '_') || 'brochure';
          var a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = title + '_HQ.pdf';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(a.href);
          showBrochureToast('Downloaded high-quality PDF!');
        })
        .catch(function (err) {
          showBrochureToast('HQ PDF error: ' + err.message);
        })
        .finally(function () {
          hqPdfBtn.disabled = false;
          hqPdfBtn.textContent = '\u2B07 HQ PDF';
        });
    });
  }

  // ── Save Template ──
  var saveTemplateBtn = document.getElementById('saveTemplateBtn');
  if (saveTemplateBtn) {
    saveTemplateBtn.addEventListener('click', function () {
      var projectId = window.__PROJECT_ID__;
      if (!projectId) {
        showBrochureToast('Cannot save template — no project ID found.');
        return;
      }

      var name = prompt('Template name:', document.title.split(' — ')[0] || 'My Template');
      if (!name) return;

      var formData = new FormData();
      formData.append('name', name);
      formData.append('project_id', projectId);

      saveTemplateBtn.disabled = true;
      saveTemplateBtn.textContent = '⏳ Saving...';

      fetch('/api/templates/save', { method: 'POST', body: formData })
        .then(function (res) {
          if (res.ok) {
            return res.json().then(function (data) {
              showBrochureToast('Template "' + name + '" saved successfully.');
            });
          } else {
            return res.text().then(function (text) {
              try {
                var data = JSON.parse(text);
                showBrochureToast('Error: ' + (data.detail || 'Failed to save template.'));
              } catch (e) {
                showBrochureToast('Error: Failed to save template (server error).');
              }
            });
          }
        })
        .catch(function (err) {
          showBrochureToast('Network error: ' + err.message);
        })
        .finally(function () {
          saveTemplateBtn.disabled = false;
          saveTemplateBtn.textContent = '★ Save Template';
        });
    });
  }

  // ── Download Clean HTML (Presentation Mode) ──
  var downloadHtmlBtn = document.getElementById('downloadHtmlBtn');
  if (downloadHtmlBtn) {
    downloadHtmlBtn.addEventListener('click', function () {
      var projectId = window.__PROJECT_ID__;

      // If we have a project ID, fetch server-rendered clean HTML
      if (projectId) {
        downloadHtmlBtn.disabled = true;
        downloadHtmlBtn.textContent = 'Exporting...';

        fetch('/api/projects/' + projectId + '/export/html')
          .then(function (res) {
            if (!res.ok) throw new Error('Export failed');
            return res.text();
          })
          .then(function (html) {
            var title = document.title.replace(/[^a-zA-Z0-9 \-_]/g, '').trim().replace(/\s+/g, '_') || 'brochure';
            var filename = title + '_clean.html';
            var blob = new Blob([html], { type: 'text/html;charset=utf-8' });
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showBrochureToast('Downloaded clean HTML: "' + filename + '"');
          })
          .catch(function (err) {
            // Fallback: client-side clean export
            downloadCleanHtmlClientSide();
          })
          .finally(function () {
            downloadHtmlBtn.disabled = false;
            downloadHtmlBtn.textContent = '\uD83D\uDCBE Download HTML';
          });
      } else {
        downloadCleanHtmlClientSide();
      }
    });
  }

  function downloadCleanHtmlClientSide() {
    // Clone the full document
    var clone = document.documentElement.cloneNode(true);

    // Remove editor UI elements
    var removeSelectors = [
      '.slide-nav', '.settings-panel', '.icon-picker-overlay',
      '.line-picker-overlay', '#pdfOverlay', '.slide-label',
      '.slide-manager-bar', '.text-toolbar', '.ai-rewrite-toolbar',
      '.layout-chooser', '[data-layout-editor-only]',
      '.route-delete-btn', '.add-line-btn', '.tube-add-btn',
      '#brochureToast', '#brToastStyle',
      '.slides-dropdown-panel', '.chat-sidebar',
      '.chat-minimized-tab', '.map-generate-btn'
    ];
    removeSelectors.forEach(function (sel) {
      clone.querySelectorAll(sel).forEach(function (el) { el.remove(); });
    });

    // Remove hidden slides
    clone.querySelectorAll('.slide-wrapper.slide-hidden').forEach(function (el) { el.remove(); });

    // Remove all script tags
    clone.querySelectorAll('script').forEach(function (s) { s.remove(); });

    // Remove contenteditable attributes
    clone.querySelectorAll('[contenteditable]').forEach(function (el) {
      el.removeAttribute('contenteditable');
    });

    // Remove editor-specific hover/focus styles
    clone.querySelectorAll('.highlight-icon').forEach(function (el) {
      el.style.cursor = 'default';
    });
    clone.querySelectorAll('.img-placeholder').forEach(function (el) {
      el.style.cursor = 'default';
    });

    // Fix body padding (remove nav bar offset)
    var body = clone.querySelector('body');
    if (body) body.style.paddingTop = '24px';

    var title = document.title.replace(/[^a-zA-Z0-9 \-_]/g, '').trim().replace(/\s+/g, '_') || 'brochure';
    var filename = title + '_clean.html';

    var blob = new Blob(['<!DOCTYPE html>\n' + clone.outerHTML], { type: 'text/html;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    showBrochureToast('Downloaded clean HTML: "' + filename + '"');
  }

  // ── LinkedIn Carousel Export ──
  var linkedinExportBtn = document.getElementById('linkedinExportBtn');
  if (linkedinExportBtn) {
    linkedinExportBtn.addEventListener('click', function () {
      var projectId = window.__PROJECT_ID__;
      if (!projectId) {
        showBrochureToast('Cannot export — no project ID found.');
        return;
      }

      linkedinExportBtn.disabled = true;
      linkedinExportBtn.textContent = 'Generating...';
      showBrochureToast('Generating LinkedIn carousel images...');

      // Load html2canvas dynamically if not already loaded
      var ready = window.html2canvas
        ? Promise.resolve()
        : new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            script.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
            script.onload = resolve;
            script.onerror = function () { reject(new Error('Failed to load html2canvas')); };
            document.head.appendChild(script);
          });

      ready
        .then(function () {
          return fetch('/api/projects/' + projectId + '/export/linkedin');
        })
        .then(function (res) {
          if (!res.ok) throw new Error('LinkedIn export failed');
          return res.json();
        })
        .then(function (data) {
          var cards = data.cards;
          var cardNames = ['cover', 'highlights', 'photos', 'contacts'];

          // Render each card HTML into a hidden iframe, screenshot with html2canvas
          return cards.reduce(function (chain, cardHtml, idx) {
            return chain.then(function (pngs) {
              return renderCardToPng(cardHtml).then(function (blob) {
                pngs.push({ blob: blob, name: cardNames[idx] });
                return pngs;
              });
            });
          }, Promise.resolve([]));
        })
        .then(function (pngs) {
          // Download each PNG individually
          var title = document.title.replace(/[^a-zA-Z0-9 \-_]/g, '').trim().replace(/\s+/g, '_') || 'brochure';
          pngs.forEach(function (item, idx) {
            var a = document.createElement('a');
            a.href = URL.createObjectURL(item.blob);
            a.download = title + '_linkedin_' + (idx + 1) + '_' + item.name + '.png';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
          });
          showBrochureToast('Downloaded ' + pngs.length + ' LinkedIn carousel images!');
        })
        .catch(function (err) {
          showBrochureToast('LinkedIn export error: ' + err.message);
        })
        .finally(function () {
          linkedinExportBtn.disabled = false;
          linkedinExportBtn.textContent = 'in LinkedIn';
        });
    });
  }

  function renderCardToPng(cardHtml) {
    return new Promise(function (resolve, reject) {
      // Parse the card HTML to extract style and body content
      var parser = new DOMParser();
      var doc = parser.parseFromString(cardHtml, 'text/html');

      // Extract and inject scoped styles
      var styleEl = doc.querySelector('style');
      var scopedStyle = document.createElement('style');
      scopedStyle.id = 'li-card-style-tmp';
      if (styleEl) scopedStyle.textContent = styleEl.textContent;
      // Remove any previous scoped style
      var prev = document.getElementById('li-card-style-tmp');
      if (prev) prev.remove();
      document.head.appendChild(scopedStyle);

      // Create hidden render container
      var container = document.createElement('div');
      container.style.cssText = 'position:fixed;top:-9999px;left:-9999px;width:1080px;height:1080px;overflow:hidden;z-index:-1;';

      // Copy the card element into the container
      var cardEl = doc.querySelector('.li-card');
      if (!cardEl) {
        scopedStyle.remove();
        reject(new Error('Card element not found'));
        return;
      }
      container.appendChild(document.importNode(cardEl, true));
      document.body.appendChild(container);

      // Wait for fonts and rendering
      setTimeout(function () {
        var renderTarget = container.querySelector('.li-card');
        window.html2canvas(renderTarget, {
          width: 1080,
          height: 1080,
          scale: 1,
          useCORS: true,
          backgroundColor: null,
          logging: false,
        }).then(function (canvas) {
          canvas.toBlob(function (blob) {
            container.remove();
            scopedStyle.remove();
            resolve(blob);
          }, 'image/png');
        }).catch(function (err) {
          container.remove();
          scopedStyle.remove();
          reject(err);
        });
      }, 800);
    });
  }

  // ── Toast helper (brochure-level, simple) ──
  function showBrochureToast(msg) {
    // Check if a toast element already exists
    var existing = document.getElementById('brochureToast');
    if (existing) {
      existing.remove();
    }

    var toast = document.createElement('div');
    toast.id = 'brochureToast';
    toast.style.cssText = 'position:fixed;bottom:24px;right:24px;background:#1a1410;border:1px solid rgba(184,113,78,0.5);border-radius:8px;padding:14px 24px;font-family:Jost,sans-serif;font-size:13px;color:rgba(255,255,255,0.9);box-shadow:0 12px 40px rgba(0,0,0,0.5);z-index:100000;animation:brToastIn 0.3s ease;max-width:400px;';
    toast.textContent = msg;

    // Add animation keyframes if not already present
    if (!document.getElementById('brToastStyle')) {
      var style = document.createElement('style');
      style.id = 'brToastStyle';
      style.textContent = '@keyframes brToastIn { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }';
      document.head.appendChild(style);
    }

    document.body.appendChild(toast);

    setTimeout(function () {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity 0.3s';
      setTimeout(function () { toast.remove(); }, 300);
    }, 4000);
  }

  // Expose toast globally for other modules
  window.showBrochureToast = showBrochureToast;
});
