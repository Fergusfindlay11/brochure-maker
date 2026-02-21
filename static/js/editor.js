/**
 * Image Upload Editor — click or drag onto any .img-placeholder to upload images.
 * Optionally POSTs to /api/projects/{id}/images if project context exists.
 */
document.addEventListener('DOMContentLoaded', function () {

  document.querySelectorAll('.img-placeholder').forEach(function (placeholder) {

    // Drag-over highlight
    placeholder.addEventListener('dragover', function (e) {
      e.preventDefault();
      placeholder.style.background = 'rgba(184,113,78,0.25)';
      placeholder.style.borderColor = 'rgba(255,255,255,0.6)';
    });

    // Drag-leave reset
    placeholder.addEventListener('dragleave', function () {
      placeholder.style.background = '';
      placeholder.style.borderColor = '';
    });

    // Drop handler
    placeholder.addEventListener('drop', function (e) {
      e.preventDefault();
      placeholder.style.background = '';
      placeholder.style.borderColor = '';
      var file = e.dataTransfer.files[0];
      if (file && file.type.startsWith('image/')) {
        var reader = new FileReader();
        reader.onload = function (ev) {
          placeholder.querySelectorAll('.ph-icon, .ph-label').forEach(function (el) { el.style.display = 'none'; });
          placeholder.style.backgroundImage = 'url(' + ev.target.result + ')';
          placeholder.style.backgroundSize = 'cover';
          placeholder.style.backgroundPosition = 'center';
          placeholder.style.border = 'none';
          placeholder.style.cursor = 'default';
        };
        reader.readAsDataURL(file);
      }
    });

    // Click-to-upload handler
    placeholder.addEventListener('click', function () {
      var input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.onchange = function (e) {
        var file = e.target.files[0];
        if (file) {
          var reader = new FileReader();
          reader.onload = function (ev) {
            placeholder.querySelectorAll('.ph-icon, .ph-label').forEach(function (el) { el.style.display = 'none'; });
            placeholder.style.backgroundImage = 'url(' + ev.target.result + ')';
            placeholder.style.backgroundSize = 'cover';
            placeholder.style.backgroundPosition = 'center';
            placeholder.style.border = 'none';
          };
          reader.readAsDataURL(file);
        }
      };
      input.click();
    });
  });

});
