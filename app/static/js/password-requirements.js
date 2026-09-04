// Shows the password length requirement inline and blocks submission
// client-side when it isn't met, so a rejected password doesn't cost
// the user a full round-trip (and, on forms like onboarding that don't
// re-render with prior input, doesn't wipe everything else they typed).
//
// Usage: add data-password-field + data-min-length="12" to a <input
// type="password">. Add data-password-confirm-of="<id-of-password-field>"
// to a confirm field to also check it matches.
(function () {
  function addHint(field, minLength) {
    var hint = document.createElement('p');
    hint.className = 'subdued password-requirement-hint';
    hint.style.cssText = 'margin:4px 0 0; font-size:12px;';
    hint.textContent = 'Must be at least ' + minLength + ' characters.';
    field.insertAdjacentElement('afterend', hint);
    return hint;
  }

  function addError(field, message) {
    clearError(field);
    var err = document.createElement('p');
    err.className = 'password-requirement-error';
    err.style.cssText = 'margin:4px 0 0; font-size:12px; color:var(--danger);';
    err.textContent = message;
    field.insertAdjacentElement('afterend', err);
  }

  function clearError(field) {
    var next = field.nextElementSibling;
    if (next && next.classList.contains('password-requirement-error')) {
      next.remove();
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    var fields = document.querySelectorAll('[data-password-field]');
    fields.forEach(function (field) {
      var minLength = parseInt(field.getAttribute('data-min-length'), 10) || 12;
      addHint(field, minLength);
      field.addEventListener('input', function () { clearError(field); });
    });

    var confirmFields = document.querySelectorAll('[data-password-confirm-of]');
    confirmFields.forEach(function (field) {
      field.addEventListener('input', function () { clearError(field); });
    });

    var forms = document.querySelectorAll('form');
    forms.forEach(function (form) {
      var passwordFields = form.querySelectorAll('[data-password-field]');
      if (!passwordFields.length) return;

      form.addEventListener('submit', function (e) {
        var firstInvalid = null;

        passwordFields.forEach(function (field) {
          clearError(field);
          var minLength = parseInt(field.getAttribute('data-min-length'), 10) || 12;
          // Optional password fields (e.g. "leave blank to keep current
          // password" on the profile page) are only checked if filled in.
          var required = field.hasAttribute('required');
          if (!field.value && !required) return;
          if (field.value.length < minLength) {
            addError(field, 'Password must be at least ' + minLength + ' characters.');
            firstInvalid = firstInvalid || field;
          }
        });

        confirmFields.forEach(function (field) {
          var sourceId = field.getAttribute('data-password-confirm-of');
          var source = document.getElementById(sourceId);
          if (!source) return;
          if (!field.value && !source.value) return;
          if (field.value !== source.value) {
            addError(field, "Passwords don't match.");
            firstInvalid = firstInvalid || field;
          }
        });

        if (firstInvalid) {
          e.preventDefault();
          firstInvalid.focus();
        }
      });
    });
  });
})();
