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

  // ── Geocode / Save Location button ──
  var geocodeBtn = document.getElementById('geocodeBtn');
  var geocodeStatus = document.getElementById('geocodeStatus');

  if (geocodeBtn) {
    geocodeBtn.addEventListener('click', function () {
      var address  = document.getElementById('bldgAddressInput').value.trim();
      var postcode = document.getElementById('bldgPostcodeInput').value.trim();
      var city     = document.getElementById('bldgLocationInput').value.trim();
      var projectId = window.__PROJECT_ID__;

      if (!address && !postcode) {
        geocodeStatus.textContent = 'Enter an address or postcode first.';
        geocodeStatus.style.color = 'rgba(252,165,165,0.9)';
        return;
      }

      geocodeBtn.disabled = true;
      geocodeStatus.textContent = 'Locating\u2026';
      geocodeStatus.style.color = 'rgba(255,255,255,0.5)';

      fetch('/api/projects/' + projectId + '/geocode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: address, postcode: postcode, city: city }),
      })
        .then(function (r) {
          return r.ok
            ? r.json()
            : r.json().then(function (d) { throw new Error(d.detail || 'Geocode failed'); });
        })
        .then(function (data) {
          geocodeStatus.textContent = '\u2713 ' + (data.display_name || 'Location saved');
          geocodeStatus.style.color = 'rgba(134,239,172,0.9)';
        })
        .catch(function (err) {
          geocodeStatus.textContent = '\u2717 ' + err.message;
          geocodeStatus.style.color = 'rgba(252,165,165,0.9)';
        })
        .finally(function () { geocodeBtn.disabled = false; });
    });
  }
});
