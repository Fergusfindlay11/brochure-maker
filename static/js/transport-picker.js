/**
 * Transport / TFL Line Picker — line data, line picker overlay, station rows.
 */
document.addEventListener('DOMContentLoaded', function () {

  var TFL_LINES = [
    { id: 'bakerloo',     name: 'Bakerloo',          abbr: 'BA',  bg: '#996633', fg: '#fff' },
    { id: 'central',      name: 'Central',            abbr: 'C',   bg: '#E1251B', fg: '#fff' },
    { id: 'circle',       name: 'Circle',             abbr: 'CI',  bg: '#FFD329', fg: '#333' },
    { id: 'district',     name: 'District',           abbr: 'D',   bg: '#007229', fg: '#fff' },
    { id: 'dlr',          name: 'DLR',                abbr: 'DLR', bg: '#00AFAD', fg: '#fff' },
    { id: 'elizabeth',    name: 'Elizabeth',          abbr: 'E',   bg: '#6950A1', fg: '#fff' },
    { id: 'hammersmith',  name: 'Hammersmith & City', abbr: 'H',   bg: '#F3A9BB', fg: '#333' },
    { id: 'jubilee',      name: 'Jubilee',            abbr: 'J',   bg: '#7B868C', fg: '#fff' },
    { id: 'metropolitan', name: 'Metropolitan',       abbr: 'M',   bg: '#9A0059', fg: '#fff' },
    { id: 'northern',     name: 'Northern',           abbr: 'N',   bg: '#000000', fg: '#fff' },
    { id: 'overground',   name: 'Overground',         abbr: 'OV',  bg: '#E87722', fg: '#fff' },
    { id: 'piccadilly',   name: 'Piccadilly',         abbr: 'P',   bg: '#003888', fg: '#fff' },
    { id: 'thameslink',   name: 'Thameslink',         abbr: 'TL',  bg: '#E05C8C', fg: '#fff' },
    { id: 'victoria',     name: 'Victoria',           abbr: 'V',   bg: '#009BCF', fg: '#fff' },
    { id: 'waterloo',     name: 'Waterloo & City',    abbr: 'W',   bg: '#76D0BD', fg: '#333' },
    { id: 'national',     name: 'National Rail',      abbr: 'NR',  bg: '#EC4A16', fg: '#fff' },
    { id: 'tram',         name: 'Tram',               abbr: 'TR',  bg: '#84B817', fg: '#fff' },
  ];

  var LINE_MAP = {};
  TFL_LINES.forEach(function (l) { LINE_MAP[l.id] = l; });

  // Inject CSS for all lines dynamically
  var lineCSS = TFL_LINES.map(function (l) {
    return '.t-dot.' + l.id + ' { background: ' + l.bg + '; color: ' + l.fg + '; }';
  }).join('\n');
  var lineStyle = document.createElement('style');
  lineStyle.textContent = lineCSS;
  document.head.appendChild(lineStyle);

  // ── Line Picker Overlay ──
  var activeDotsContainer = null;
  var linePicker      = document.getElementById('linePickerOverlay');
  var linePickerGrid  = document.getElementById('linePickerGrid');
  var linePickerPanel = document.getElementById('linePickerPanel');

  function getSelectedLines(dotsEl) {
    return [].slice.call(dotsEl.querySelectorAll('.t-dot[data-line]')).map(function (d) { return d.dataset.line; });
  }

  function renderLinePicker(dotsEl) {
    var selected = getSelectedLines(dotsEl);
    linePickerGrid.innerHTML = TFL_LINES.map(function (l) {
      return '<div class="line-opt' + (selected.includes(l.id) ? ' selected' : '') + '" data-line-id="' + l.id + '">' +
        '<div class="line-dot-preview" style="background:' + l.bg + ';color:' + l.fg + '">' + l.abbr + '</div>' +
        '<div class="line-opt-name">' + l.name + '</div>' +
        '</div>';
    }).join('');
    linePickerGrid.querySelectorAll('.line-opt').forEach(function (opt) {
      opt.addEventListener('click', function (e) {
        e.stopPropagation();
        opt.classList.toggle('selected');
      });
    });
  }

  function applyLinesToDots(dotsEl) {
    var addBtn = dotsEl.querySelector('.add-line-btn');
    var selected = [].slice.call(linePickerGrid.querySelectorAll('.line-opt.selected')).map(function (o) { return o.dataset.lineId; });
    // Remove existing dots
    dotsEl.querySelectorAll('.t-dot[data-line]').forEach(function (d) { d.remove(); });
    // Re-add in TFL order
    TFL_LINES.forEach(function (l) {
      if (selected.includes(l.id)) {
        var dot = document.createElement('div');
        dot.className = 't-dot ' + l.id;
        dot.dataset.line = l.id;
        dot.textContent = l.abbr;
        dot.title = l.name;
        attachDotClickHandler(dot);
        dotsEl.insertBefore(dot, addBtn);
      }
    });
  }

  function openLinePicker(dotsEl, e) {
    e.stopPropagation();
    activeDotsContainer = dotsEl;
    renderLinePicker(dotsEl);
    linePicker.classList.add('open');
    var rect = (e.target.closest('.t-dot') || e.target).getBoundingClientRect();
    var top = rect.bottom + 8, left = rect.left - 60;
    if (left < 8) left = 8;
    if (left + 270 > window.innerWidth) left = window.innerWidth - 278;
    if (top + 500 > window.innerHeight) top = rect.top - 510;
    linePickerPanel.style.top = top + 'px';
    linePickerPanel.style.left = left + 'px';
  }

  function closeLinePicker() {
    linePicker.classList.remove('open');
    activeDotsContainer = null;
  }

  document.getElementById('linePickerDone').addEventListener('click', function (e) {
    e.stopPropagation();
    if (activeDotsContainer) applyLinesToDots(activeDotsContainer);
    closeLinePicker();
  });

  linePicker.addEventListener('click', function (e) {
    if (e.target === linePicker) closeLinePicker();
  });

  // Also close on Escape
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeLinePicker();
  });

  function attachDotClickHandler(dot) {
    dot.addEventListener('click', function (e) {
      var dotsEl = dot.closest('.transport-dots');
      openLinePicker(dotsEl, e);
    });
  }

  // Attach to all existing dots
  document.querySelectorAll('.t-dot[data-line]').forEach(attachDotClickHandler);

  // Add line button
  document.querySelectorAll('.add-line-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      var dotsEl = btn.closest('.transport-dots');
      openLinePicker(dotsEl, e);
    });
  });

  // ============================================================
  // ADD STATION BUTTON
  // ============================================================
  function buildStationRow(name, lines) {
    name  = name  || 'NEW STATION \u2014 0 Mins';
    lines = lines || [];

    var row = document.createElement('div');
    row.className = 'travel-route';
    row.setAttribute('data-station', '');

    var delBtn = document.createElement('button');
    delBtn.className = 'route-delete-btn';
    delBtn.title = 'Remove station';
    delBtn.textContent = '\u2715';
    delBtn.addEventListener('click', function () { row.remove(); });
    row.appendChild(delBtn);

    var nameEl = document.createElement('div');
    nameEl.className = 'route-name';
    nameEl.contentEditable = 'true';
    nameEl.textContent = name;
    row.appendChild(nameEl);

    var dotsEl = document.createElement('div');
    dotsEl.className = 'transport-dots';

    lines.forEach(function (lid) {
      var l = LINE_MAP[lid];
      if (!l) return;
      var dot = document.createElement('div');
      dot.className = 't-dot ' + l.id;
      dot.dataset.line = l.id;
      dot.textContent = l.abbr;
      dot.title = l.name;
      attachDotClickHandler(dot);
      dotsEl.appendChild(dot);
    });

    var addBtn = document.createElement('div');
    addBtn.className = 'add-line-btn';
    addBtn.title = 'Add line';
    addBtn.textContent = '+';
    addBtn.addEventListener('click', function (e) { openLinePicker(dotsEl, e); });
    dotsEl.appendChild(addBtn);

    row.appendChild(dotsEl);
    return row;
  }

  document.getElementById('addStationBtn').addEventListener('click', function () {
    var list = document.getElementById('stationList');
    var row = buildStationRow();
    list.appendChild(row);
    row.querySelector('.route-name').focus();
  });

  // Wire up existing delete buttons
  document.querySelectorAll('.route-delete-btn').forEach(function (btn) {
    btn.addEventListener('click', function () { btn.closest('.travel-route').remove(); });
  });

  // ── City Selector (Dynamic Transport Networks) ──
  var citySelector = document.getElementById('citySelector');
  var cityCache = {};

  if (citySelector) {
    citySelector.addEventListener('change', function () {
      var city = this.value;
      loadCityLines(city);
    });
  }

  function loadCityLines(city) {
    if (cityCache[city]) {
      applyCityLines(cityCache[city]);
      return;
    }

    fetch('/static/data/transport/' + city + '.json')
      .then(function (res) {
        if (!res.ok) throw new Error('City data not found');
        return res.json();
      })
      .then(function (data) {
        // Convert JSON format to internal format
        var lines = data.lines.map(function (l) {
          var id = l.name.toLowerCase().replace(/[^a-z0-9]/g, '_');
          return {
            id: id,
            name: l.name,
            abbr: l.abbr,
            bg: l.colour,
            fg: isLightColor(l.colour) ? '#333' : '#fff',
          };
        });
        cityCache[city] = lines;
        applyCityLines(lines);
      })
      .catch(function () {
        // Fallback: keep current lines
      });
  }

  function applyCityLines(newLines) {
    // Update TFL_LINES and LINE_MAP
    TFL_LINES.length = 0;
    newLines.forEach(function (l) { TFL_LINES.push(l); });
    // Rebuild LINE_MAP
    Object.keys(LINE_MAP).forEach(function (k) { delete LINE_MAP[k]; });
    TFL_LINES.forEach(function (l) { LINE_MAP[l.id] = l; });
    // Update CSS
    lineStyle.textContent = TFL_LINES.map(function (l) {
      return '.t-dot.' + l.id + ' { background: ' + l.bg + '; color: ' + l.fg + '; }';
    }).join('\n');
    // Re-render picker if open
    if (activeDotsContainer) renderLinePicker(activeDotsContainer);
  }

  function isLightColor(hex) {
    hex = hex.replace('#', '');
    var r = parseInt(hex.substr(0, 2), 16);
    var g = parseInt(hex.substr(2, 2), 16);
    var b = parseInt(hex.substr(4, 2), 16);
    return (r * 299 + g * 587 + b * 114) / 1000 > 160;
  }

  // Expose for other modules
  window.TFL_LINES     = TFL_LINES;
  window.LINE_MAP      = LINE_MAP;
  window.closeLinePicker = closeLinePicker;
  window.buildStationRow = buildStationRow;
});
