/**
 * Neighbourhood Map Generator
 * Handles "Generate Map" button on the travel slide.
 * Calls /api/projects/{id}/generate-map and applies the result.
 *
 * Illustrated maps are returned as inline SVG (transparent background,
 * adapts to CSS colour scheme changes automatically).
 * Leaflet fallback returns a PNG data URL applied as backgroundImage.
 */
document.addEventListener('DOMContentLoaded', function () {
  var projectId = window.__PROJECT_ID__;
  if (!projectId) return;

  var generateBtn = document.getElementById('generateMapBtn');
  if (!generateBtn) return;

  generateBtn.addEventListener('click', function (e) {
    e.stopPropagation(); // Prevent img-placeholder click handler

    if (generateBtn.disabled) return;
    generateBtn.disabled = true;
    var originalText = generateBtn.innerHTML;
    generateBtn.innerHTML = '&#9203; Generating\u2026';
    generateBtn.classList.add('map-loading');

    if (window.showBrochureToast) {
      window.showBrochureToast('Generating neighbourhood map\u2026 this may take a moment.');
    }

    // Read current primary colour from CSS variable
    var primaryColour = getComputedStyle(document.documentElement)
      .getPropertyValue('--primary').trim();

    // Read actual background colour of the map's slide wrapper
    var slideWrapper = generateBtn.closest('.slide-wrapper');
    var computedBg = slideWrapper
      ? getComputedStyle(slideWrapper).backgroundColor
      : '';
    var baseHex = primaryColour; // fallback
    if (computedBg && computedBg.indexOf('rgb') === 0) {
      var parts = computedBg.match(/\d+/g);
      if (parts && parts.length >= 3) {
        baseHex = '#' +
          ('0' + parseInt(parts[0]).toString(16)).slice(-2) +
          ('0' + parseInt(parts[1]).toString(16)).slice(-2) +
          ('0' + parseInt(parts[2]).toString(16)).slice(-2);
      }
    }

    // Measure actual map container dimensions
    var mapArea = generateBtn.closest('.map-area');
    var mapWidth = mapArea ? mapArea.clientWidth : 940;
    var mapHeight = mapArea ? mapArea.clientHeight : 750;

    fetch('/api/projects/' + projectId + '/generate-map', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        primary_colour: primaryColour,
        base_hex: baseHex,
        map_width: mapWidth,
        map_height: mapHeight,
        radius_m: 350,
      }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (data) {
            throw new Error(data.detail || 'Map generation failed');
          });
        }
        return res.json();
      })
      .then(function (data) {
        var mapArea = generateBtn.closest('.map-area');
        if (!mapArea) return;

        // Hide placeholder content
        mapArea.querySelectorAll('.ph-icon, .ph-label').forEach(function (el) {
          el.style.display = 'none';
        });
        generateBtn.style.display = 'none';
        mapArea.classList.remove('img-placeholder');

        if (data.svg_content) {
          // Inline SVG mode — inject directly into DOM
          // The SVG is transparent; parent background provides the colour
          mapArea.innerHTML = data.svg_content;
          mapArea.style.backgroundImage = 'none';
          mapArea.style.background = 'transparent';
          mapArea.classList.add('map-loaded');
        } else if (data.image_data_url) {
          // Legacy PNG fallback
          mapArea.style.backgroundImage = 'url(' + data.image_data_url + ')';
          mapArea.style.backgroundSize = 'cover';
          mapArea.style.backgroundPosition = 'center';
        }
        mapArea.style.border = 'none';

        if (window.pushUndoState) window.pushUndoState();
        if (window.showBrochureToast) {
          window.showBrochureToast('Map generated successfully!');
        }
      })
      .catch(function (err) {
        if (window.showBrochureToast) {
          window.showBrochureToast('Map error: ' + err.message);
        } else {
          alert('Map generation failed: ' + err.message);
        }
      })
      .finally(function () {
        generateBtn.disabled = false;
        generateBtn.innerHTML = originalText;
        generateBtn.classList.remove('map-loading');
      });
  });
});
