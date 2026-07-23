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
    return JSON.parse(container.dataset.fieldOptions || '[]'); // [[key, label, valueType], ...]
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

  document.querySelectorAll('.condition-rows').forEach(function (container) {
    container.addEventListener('change', function (e) {
      if (!e.target.classList.contains('condition-field-select')) return;
      var row = e.target.closest('.condition-row');
      var opSelect = row.querySelector('.condition-operator-select');
      populateOperatorSelect(container, opSelect, e.target.value, null);
    });

    container.addEventListener('click', function (e) {
      var removeBtn = e.target.closest('.condition-remove-btn');
      if (removeBtn) removeBtn.closest('.condition-row').remove();
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

      var valueInput = document.createElement('input');
      valueInput.type = 'text';
      valueInput.name = 'condition_value';
      valueInput.className = 'condition-value-input';
      valueInput.placeholder = 'value';

      var removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'condition-remove-btn';
      removeBtn.setAttribute('aria-label', 'Remove condition');
      removeBtn.textContent = '\u00d7';

      row.appendChild(fieldSelect);
      row.appendChild(opSelect);
      row.appendChild(valueInput);
      row.appendChild(removeBtn);
      container.appendChild(row);

      populateOperatorSelect(container, opSelect, fields[0][0], null);
    });
  });
})();
