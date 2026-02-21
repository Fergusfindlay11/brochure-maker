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
            return res.json().then(function (data) {
              showBrochureToast('Error: ' + (data.detail || 'Failed to save template.'));
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

  // ── Download HTML ──
  var downloadHtmlBtn = document.getElementById('downloadHtmlBtn');
  if (downloadHtmlBtn) {
    downloadHtmlBtn.addEventListener('click', function () {
      // Clone the document to remove editor-only UI before download
      var html = document.documentElement.outerHTML;

      // Build a clean filename from the page title
      var title = document.title.replace(/[^a-zA-Z0-9 \-_]/g, '').trim().replace(/\s+/g, '_') || 'brochure';
      var filename = title + '.html';

      // Create download
      var blob = new Blob(['<!DOCTYPE html>\n' + html], { type: 'text/html;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      showBrochureToast('Downloaded "' + filename + '"');
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
});
