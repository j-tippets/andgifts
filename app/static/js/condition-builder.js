// Generic condition builder: each ".condition-rows" container holds
// removable rows of (field, operator, value). Progressively enhances a
// server-rendered set of rows -- if this script fails to load, the
// existing rows still submit fine as a normal form; only "+ Add a
// condition" needs JS to work.
(function () {
  function operatorOptionsFor(container, fieldKey) {
    var map = JSON.parse(container.dataset.operatorMap || '{}');
    return map[fieldKey] || [];
  }

  function fieldOptionsFor(container) {
    return JSON.parse(container.dataset.fieldOptions || '[]'); // [[key, label, valueType, options], ...]
  }

  function valueOptionsFor(container, fieldKey) {
    var fields = fieldOptionsFor(container);
    for (var i = 0; i < fields.length; i++) {
      if (fields[i][0] === fieldKey) return fields[i][3] || [];
    }
    return [];
  }

  function populateOperatorSelect(container, opSelect, fieldKey, keepValue) {
    var options = operatorOptionsFor(container, fieldKey);
    opSelect.innerHTML = '';
    options.forEach(function (pair) {
      var opt = document.createElement('option');
      opt.value = pair[0];
      opt.textContent = pair[1];
      if (pair[0] === keepValue) opt.selected = true;
      opSelect.appendChild(opt);
    });
  }

  // The value control swaps between a free-text <input> and a <select>
  // depending on whether the currently-chosen field has a fixed,
  // known set of valid values (badges, interest tags, select-type
  // custom fields, checkbox) -- see campaign_rules.condition_field_choices.
  // Since that can change every time a different field is picked, this
  // replaces the control outright rather than trying to morph one
  // element's type in place.
  function buildValueControl(container, fieldKey, keepValue) {
    var valueOptions = valueOptionsFor(container, fieldKey);
    var control;
    if (valueOptions.length) {
      control = document.createElement('select');
      valueOptions.forEach(function (pair) {
        var opt = document.createElement('option');
        opt.value = pair[0];
        opt.textContent = pair[1];
        if (pair[0] === keepValue) opt.selected = true;
        control.appendChild(opt);
      });
    } else {
      control = document.createElement('input');
      control.type = 'text';
      control.placeholder = 'value';
      if (keepValue != null) control.value = keepValue;
    }
    control.name = 'condition_value';
    control.className = 'condition-value-input';
    return control;
  }

  function replaceValueControl(row, container, fieldKey, keepValue) {
    var old = row.querySelector('.condition-value-input');
    var replacement = buildValueControl(container, fieldKey, keepValue);
    if (old) {
      old.replaceWith(replacement);
    } else {
      row.insertBefore(replacement, row.querySelector('.condition-remove-btn'));
    }
  }

  // "Everyone who qualifies" indicator: shown whenever a condition-rows
  // container has zero rows, hidden as soon as one is added. Each
  // container may have its own indicator (id="<container-id>-indicator")
  // -- currently only the wizard's main condition builder has one.
  function refreshEveryoneIndicator(container) {
    var indicator = document.getElementById(container.id + '-indicator') ||
      document.getElementById('everyone-indicator');
    if (!indicator) return;
    var hasRows = container.querySelectorAll('.condition-row').length > 0;
    indicator.style.display = hasRows ? 'none' : 'inline-block';
  }
  window.refreshEveryoneIndicator = refreshEveryoneIndicator;

  document.querySelectorAll('.condition-rows').forEach(function (container) {
    refreshEveryoneIndicator(container);

    container.addEventListener('change', function (e) {
      if (!e.target.classList.contains('condition-field-select')) return;
      var row = e.target.closest('.condition-row');
      var opSelect = row.querySelector('.condition-operator-select');
      populateOperatorSelect(container, opSelect, e.target.value, null);
      // A field change means the value's meaning changed entirely
      // (e.g. switching from a badge to a number field), so the old
      // typed/selected value doesn't carry over -- same as the
      // operator reset just above.
      replaceValueControl(row, container, e.target.value, null);
    });

    container.addEventListener('click', function (e) {
      var removeBtn = e.target.closest('.condition-remove-btn');
      if (removeBtn) {
        removeBtn.closest('.condition-row').remove();
        refreshEveryoneIndicator(container);
      }
    });
  });

  document.querySelectorAll('.condition-add-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var container = document.getElementById(btn.dataset.target);
      var fields = fieldOptionsFor(container);
      if (!fields.length) return;

      var row = document.createElement('div');
      row.className = 'condition-row';

      var fieldSelect = document.createElement('select');
      fieldSelect.name = 'condition_field';
      fieldSelect.className = 'condition-field-select';
      fields.forEach(function (f) {
        var opt = document.createElement('option');
        opt.value = f[0];
        opt.textContent = f[1];
        fieldSelect.appendChild(opt);
      });

      var opSelect = document.createElement('select');
      opSelect.name = 'condition_operator';
      opSelect.className = 'condition-operator-select';

      var removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'condition-remove-btn';
      removeBtn.setAttribute('aria-label', 'Remove condition');
      removeBtn.textContent = '\u00d7';

      row.appendChild(fieldSelect);
      row.appendChild(opSelect);
      row.appendChild(buildValueControl(container, fields[0][0], null));
      row.appendChild(removeBtn);
      container.appendChild(row);

      populateOperatorSelect(container, opSelect, fields[0][0], null);
      refreshEveryoneIndicator(container);
    });
  });
})();
