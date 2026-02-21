/**
 * Settings Panel — building name, location, subtitle, page title sync.
 */
document.addEventListener('DOMContentLoaded', function () {

  // Settings panel toggle
  var settingsPanel = document.getElementById('settingsPanel');

  document.getElementById('settingsToggle').addEventListener('click', function (e) {
    e.stopPropagation();
    settingsPanel.classList.toggle('open');
  });

  document.getElementById('settingsClose').addEventListener('click', function () {
    settingsPanel.classList.remove('open');
  });

  document.addEventListener('click', function (e) {
    if (!settingsPanel.contains(e.target) && e.target.id !== 'settingsToggle') {
      settingsPanel.classList.remove('open');
    }
  });

  // Sync building name, location, subtitle across all elements
  function syncBuildingName() {
    var name = document.getElementById('bldgNameInput').value.trim();
    var loc  = document.getElementById('bldgLocationInput').value.trim();
    var sub  = document.getElementById('bldgSubtitleInput').value.trim();
    var combined = name + (loc ? ', ' + loc.toUpperCase() : '');

    // Update all header-bar labels
    document.querySelectorAll('.bldg-name').forEach(function (el) { el.textContent = combined; });

    // Update cover title/subtitle
    var ct = document.getElementById('coverTitle');
    var cs = document.getElementById('coverSubtitle');
    if (ct && ct.textContent !== name) ct.textContent = name;
    if (cs && cs.textContent !== sub) cs.textContent = sub;

    // Update page title
    var pt = document.getElementById('pageTitleInput').value.trim();
    document.title = pt || combined;
  }

  document.getElementById('bldgNameInput').addEventListener('input', syncBuildingName);
  document.getElementById('bldgLocationInput').addEventListener('input', syncBuildingName);
  document.getElementById('bldgSubtitleInput').addEventListener('input', syncBuildingName);
  document.getElementById('pageTitleInput').addEventListener('input', function () {
    document.title = document.getElementById('pageTitleInput').value.trim();
  });

  // Expose for other modules if needed
  window.syncBuildingName = syncBuildingName;
});
