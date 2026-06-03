/**
 * Map V1 Generator
 * Flow:
 * 1) Resolve deterministic style tokens from brochure colour + optional vibe/image.
 * 2) Render SVG+PDF artifacts using /api/maps/v1/render (strictly deterministic).
 */
document.addEventListener('DOMContentLoaded', function () {
  var projectId = window.__PROJECT_ID__;
  if (!projectId) return;

  var generateBtn = document.getElementById('generateMapBtn');
  if (!generateBtn) return;

  function parseMapErrorPayload(data, fallbackMessage) {
    var detail = data && data.detail ? data.detail : data;
    var code = (detail && detail.code) || 'EXPORT_FAILED';
    var message = (detail && detail.message) || fallbackMessage || 'Map request failed';
    var hint = (detail && detail.hint) || '';
    var retryable = !!(detail && detail.retryable);
    return { code: code, message: message, hint: hint, retryable: retryable };
  }

  function readBaseColour(generateBtnEl) {
    var primaryColour = getComputedStyle(document.documentElement)
      .getPropertyValue('--primary').trim() || '#B8714E';

    var slideWrapper = generateBtnEl.closest('.slide-wrapper');
    var computedBg = slideWrapper ? getComputedStyle(slideWrapper).backgroundColor : '';
    var baseHex = primaryColour;

    if (computedBg && computedBg.indexOf('rgb') === 0) {
      var parts = computedBg.match(/\d+/g);
      if (parts && parts.length >= 3) {
        baseHex = '#'
          + ('0' + parseInt(parts[0], 10).toString(16)).slice(-2)
          + ('0' + parseInt(parts[1], 10).toString(16)).slice(-2)
          + ('0' + parseInt(parts[2], 10).toString(16)).slice(-2);
      }
    }

    return { primaryColour: primaryColour, baseHex: baseHex.toUpperCase() };
  }

  function resolveStyleTokens(mapArea, colours) {
    var vibeText = (mapArea.getAttribute('data-map-vibe') || '').trim();
    var styleImageRef = (mapArea.getAttribute('data-map-style-image-ref') || '').trim();
    var styleKey = [colours.baseHex, vibeText, styleImageRef].join('|');

    var existingKey = mapArea.getAttribute('data-map-v1-style-key') || '';
    var existingTokensRaw = mapArea.getAttribute('data-map-v1-style-tokens') || '';
    if (existingKey === styleKey && existingTokensRaw) {
      try {
        var existingTokens = JSON.parse(existingTokensRaw);
        return Promise.resolve({
          style_tokens: existingTokens,
          style_hash: mapArea.getAttribute('data-map-v1-style-hash') || '',
          style_version: mapArea.getAttribute('data-map-v1-style-version') || ''
        });
      } catch (_) {
        // fall through to API call
      }
    }

    return fetch('/api/maps/v1/style/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId,
        brochure_primary_hex: colours.baseHex,
        vibe_text: vibeText,
        style_image_ref: styleImageRef
      })
    }).then(function (res) {
      if (!res.ok) {
        return res.json().then(function (data) {
          var parsed = parseMapErrorPayload(data, 'Style generation failed');
          var err = new Error(parsed.message + (parsed.hint ? (' (' + parsed.hint + ')') : ''));
          err.mapCode = parsed.code;
          err.mapRetryable = parsed.retryable;
          throw err;
        });
      }
      return res.json();
    }).then(function (styleData) {
      mapArea.setAttribute('data-map-v1-style-key', styleKey);
      mapArea.setAttribute('data-map-v1-style-hash', styleData.style_hash || '');
      mapArea.setAttribute('data-map-v1-style-version', styleData.style_version || '');
      mapArea.setAttribute('data-map-v1-style-tokens', JSON.stringify(styleData.style_tokens || {}));
      return styleData;
    });
  }

  function fetchSvgText(url) {
    return fetch(url).then(function (res) {
      if (!res.ok) throw new Error('Could not fetch rendered SVG artifact');
      return res.text();
    });
  }

  function applyMapResponse(mapArea, generateBtnEl, renderData, svgText) {
    mapArea.querySelectorAll('.ph-icon, .ph-label').forEach(function (el) {
      el.style.display = 'none';
    });
    generateBtnEl.style.display = 'none';
    mapArea.classList.remove('img-placeholder');

    mapArea.innerHTML = svgText;
    mapArea.style.backgroundImage = 'none';
    mapArea.style.background = 'transparent';
    mapArea.classList.add('map-loaded');
    mapArea.style.border = 'none';

    mapArea.setAttribute('data-map-v1-id', renderData.map_id || '');
    mapArea.setAttribute('data-map-v1-svg-url', (renderData.artifacts || {}).svg_url || '');
    mapArea.setAttribute('data-map-v1-pdf-url', (renderData.artifacts || {}).pdf_url || '');
    mapArea.setAttribute('data-map-v1-metadata', JSON.stringify(renderData.metadata || {}));
    mapArea.setAttribute('data-map-v1-warnings', JSON.stringify(renderData.warnings || []));

    var warningFlags = ((renderData.metadata || {}).ui_warning_flags || []).join(',');
    mapArea.setAttribute('data-map-v1-warning-flags', warningFlags);
    mapArea.setAttribute('data-map-v1-needs-nudge', warningFlags.indexOf('critical_label_drop') >= 0 ? '1' : '0');

    if (window.pushUndoState) window.pushUndoState();

    if (warningFlags.indexOf('critical_label_drop') >= 0 && window.showBrochureToast) {
      window.showBrochureToast('Map generated, but some critical labels could not be placed. You can manually nudge labels.');
      return;
    }

    if (window.showBrochureToast) {
      window.showBrochureToast('Map generated successfully (SVG + PDF artifacts ready).');
    }
  }

  function renderMapV1(mapArea, styleData) {
    var mapWidth = mapArea ? mapArea.clientWidth : 940;
    var mapHeight = mapArea ? mapArea.clientHeight : 750;

    return fetch('/api/maps/v1/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId,
        extent: { radius_m: 900 },
        output: {
          width_px: mapWidth,
          height_px: mapHeight,
          include_svg: true,
          include_pdf: true
        },
        style_tokens: styleData.style_tokens,
        options: { debug: false }
      })
    }).then(function (res) {
      if (!res.ok) {
        return res.json().then(function (data) {
          var parsed = parseMapErrorPayload(data, 'Map render failed');
          var err = new Error(parsed.message + (parsed.hint ? (' (' + parsed.hint + ')') : ''));
          err.mapCode = parsed.code;
          err.mapRetryable = parsed.retryable;
          throw err;
        });
      }
      return res.json();
    });
  }

  function maybeLegacyFallback(err, mapArea, colours) {
    if (window.__MAP_V1_LEGACY_FALLBACK__ !== true) {
      return Promise.reject(err);
    }

    return fetch('/api/projects/' + projectId + '/generate-map', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        primary_colour: colours.primaryColour,
        base_hex: colours.baseHex,
        map_width: mapArea ? mapArea.clientWidth : 940,
        map_height: mapArea ? mapArea.clientHeight : 750,
        radius_m: 350,
      }),
    }).then(function (res) {
      if (!res.ok) throw err;
      return res.json();
    }).then(function (legacyData) {
      if (!legacyData.svg_content) throw err;
      return {
        _legacy: true,
        map_id: '',
        artifacts: { svg_url: '', pdf_url: '' },
        metadata: { ui_warning_flags: [] },
        warnings: [],
        svg_content: legacyData.svg_content,
      };
    });
  }

  generateBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    if (generateBtn.disabled) return;

    var mapArea = generateBtn.closest('.map-area');
    if (!mapArea) return;

    generateBtn.disabled = true;
    var originalText = generateBtn.innerHTML;
    generateBtn.innerHTML = '&#9203; Generating\u2026';
    generateBtn.classList.add('map-loading');

    if (window.showBrochureToast) {
      window.showBrochureToast('Generating neighbourhood map\u2026 this may take a moment.');
    }

    var colours = readBaseColour(generateBtn);

    resolveStyleTokens(mapArea, colours)
      .then(function (styleData) {
        mapArea.setAttribute('data-map-v1-style-hash', styleData.style_hash || '');
        mapArea.setAttribute('data-map-v1-style-version', styleData.style_version || '');
        mapArea.setAttribute('data-map-v1-style-tokens', JSON.stringify(styleData.style_tokens || {}));
        return renderMapV1(mapArea, styleData);
      })
      .catch(function (err) {
        return maybeLegacyFallback(err, mapArea, colours);
      })
      .then(function (renderData) {
        if (renderData._legacy) {
          applyMapResponse(mapArea, generateBtn, renderData, renderData.svg_content || '');
          return;
        }

        var svgUrl = (renderData.artifacts || {}).svg_url;
        if (!svgUrl) throw new Error('Render completed without svg_url');

        return fetchSvgText(svgUrl).then(function (svgText) {
          applyMapResponse(mapArea, generateBtn, renderData, svgText);
        });
      })
      .catch(function (err) {
        var retryHint = err.mapRetryable ? ' You can retry.' : '';
        var codePrefix = err.mapCode ? ('[' + err.mapCode + '] ') : '';
        if (window.showBrochureToast) {
          window.showBrochureToast('Map error: ' + codePrefix + err.message + retryHint);
        } else {
          alert('Map generation failed: ' + codePrefix + err.message + retryHint);
        }
      })
      .finally(function () {
        generateBtn.disabled = false;
        generateBtn.innerHTML = originalText;
        generateBtn.classList.remove('map-loading');
      });
  });
});
