/**
 * V1 Map Pipeline Client
 *
 * Two-step flow:
 * 1. Call /api/maps/v1/style/generate to get style_tokens from primary colour.
 * 2. Call /api/maps/v1/render with those tokens + center/extent.
 *
 * Caches style_tokens in editor state so re-renders skip step 1.
 * Shows non-blocking warnings when critical labels are dropped.
 */
document.addEventListener('DOMContentLoaded', function () {
  var projectId = window.__PROJECT_ID__;
  if (!projectId) return;

  // Cached style tokens (persisted via auto-save)
  var cachedStyleTokens = null;
  var cachedStyleHash = '';

  // Expose for auto-save persistence
  window.__mapStyleTokens = null;
  window.__mapStyleHash = '';

  var generateBtn = document.getElementById('generateMapBtnV1');
  if (!generateBtn) {
    // Fall back to existing button if V1 button not present
    generateBtn = document.getElementById('generateMapBtn');
  }
  if (!generateBtn) return;

  // Mark as V1 pipeline
  generateBtn.setAttribute('data-pipeline', 'v1');

  generateBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    if (generateBtn.disabled) return;
    generateBtn.disabled = true;
    var originalText = generateBtn.innerHTML;
    generateBtn.innerHTML = '&#9203; Generating\u2026';
    generateBtn.classList.add('map-loading');

    if (window.showBrochureToast) {
      window.showBrochureToast('Generating map with v1 pipeline\u2026');
    }

    var primaryColour = getComputedStyle(document.documentElement)
      .getPropertyValue('--primary').trim();

    var mapArea = generateBtn.closest('.map-area');
    var mapWidth = mapArea ? mapArea.clientWidth : 940;
    var mapHeight = mapArea ? mapArea.clientHeight : 750;

    // Read analysis data for center coordinates and building info
    _getAnalysis(projectId)
      .then(function (analysis) {
        var lat = analysis.lat;
        var lng = analysis.lng;

        if (!lat || !lng) {
          throw new Error('No coordinates available. Geocode the address first.');
        }

        var buildingName = analysis.brochure_name || '';
        var stations = [];
        (analysis.slides || []).forEach(function (slide) {
          if (slide.type === 'travel_map') {
            stations = (slide.content || {}).stations || [];
          }
        });

        return _generateMapV1({
          primaryColour: primaryColour,
          lat: lat,
          lng: lng,
          mapWidth: mapWidth,
          mapHeight: mapHeight,
          buildingName: buildingName,
          stations: stations,
        });
      })
      .then(function (data) {
        _applyMapResult(generateBtn, data);
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


  /**
   * Two-step map generation: style -> render.
   */
  function _generateMapV1(opts) {
    // Step 1: Get style tokens (use cached if primary hasn't changed)
    var stylePromise;

    if (cachedStyleTokens && window.__mapStyleHash) {
      stylePromise = Promise.resolve({
        style_tokens: cachedStyleTokens,
        style_hash: window.__mapStyleHash,
      });
    } else {
      stylePromise = fetch('/api/maps/v1/style/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brochure_primary_hex: opts.primaryColour,
        }),
      })
        .then(function (res) {
          if (!res.ok) {
            return res.json().then(function (d) {
              throw new Error(d.detail || 'Style generation failed');
            });
          }
          return res.json();
        })
        .then(function (styleResp) {
          cachedStyleTokens = styleResp.style_tokens;
          cachedStyleHash = styleResp.style_hash;
          window.__mapStyleTokens = styleResp.style_tokens;
          window.__mapStyleHash = styleResp.style_hash;
          return styleResp;
        });
    }

    // Step 2: Render with resolved tokens
    return stylePromise.then(function (styleResp) {
      return fetch('/api/maps/v1/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          center: { lat: opts.lat, lng: opts.lng },
          extent: {
            radius_m: 350,
            width_px: opts.mapWidth,
            height_px: opts.mapHeight,
          },
          output: { format: 'svg' },
          content: {
            building_name: opts.buildingName,
            stations: opts.stations,
          },
          style_tokens: styleResp.style_tokens,
        }),
      })
        .then(function (res) {
          if (!res.ok) {
            return res.json().then(function (d) {
              throw new Error(
                (typeof d.detail === 'string' ? d.detail : d.detail.detail) ||
                  'Render failed'
              );
            });
          }
          return res.json();
        });
    });
  }


  /**
   * Apply the render result to the map area.
   */
  function _applyMapResult(btn, data) {
    var mapArea = btn.closest('.map-area');
    if (!mapArea) return;

    // Hide placeholder content
    mapArea.querySelectorAll('.ph-icon, .ph-label').forEach(function (el) {
      el.style.display = 'none';
    });
    btn.style.display = 'none';
    mapArea.classList.remove('img-placeholder');

    if (data.svg_content) {
      mapArea.innerHTML = data.svg_content;
      mapArea.style.backgroundImage = 'none';
      mapArea.style.background = 'transparent';
      mapArea.classList.add('map-loaded');
    }
    mapArea.style.border = 'none';

    // Show warnings for critical label drops
    if (data.ui_warning_flags && data.ui_warning_flags.length > 0) {
      data.ui_warning_flags.forEach(function (flag) {
        if (window.showBrochureToast) {
          window.showBrochureToast('\u26A0 ' + flag.message);
        }
      });
    }

    if (window.pushUndoState) window.pushUndoState();
    if (window.showBrochureToast) {
      window.showBrochureToast('Map generated successfully (v1 pipeline)!');
    }
  }


  /**
   * Fetch analysis.json for the project.
   */
  function _getAnalysis(projectId) {
    return fetch('/api/projects/' + projectId + '/status/poll')
      .then(function (res) { return res.json(); })
      .then(function () {
        // Analysis is embedded in project state; read editor state or refetch
        return fetch('/api/projects/' + projectId + '/state')
          .then(function (res) {
            if (res.ok) return res.json();
            return {};
          })
          .then(function (state) {
            // Try to get lat/lng from saved state or from DOM
            if (state && state.lat && state.lng) {
              return state;
            }
            // Read from window if available
            if (window.__ANALYSIS_DATA) {
              return window.__ANALYSIS_DATA;
            }
            // Fallback: read analysis from the page
            return _fetchAnalysisFromPage(projectId);
          });
      });
  }


  function _fetchAnalysisFromPage(projectId) {
    // The analysis data is available in the brochure HTML as embedded JSON
    var dataEl = document.getElementById('analysisData');
    if (dataEl) {
      try { return JSON.parse(dataEl.textContent); } catch (e) { /* ignore */ }
    }
    // Last resort: try to get from the map generate endpoint (legacy)
    return { lat: null, lng: null, brochure_name: '', slides: [] };
  }


  /**
   * Invalidate cached tokens when colour changes.
   */
  window.invalidateMapStyleCache = function () {
    cachedStyleTokens = null;
    cachedStyleHash = '';
    window.__mapStyleTokens = null;
    window.__mapStyleHash = '';
  };
});
