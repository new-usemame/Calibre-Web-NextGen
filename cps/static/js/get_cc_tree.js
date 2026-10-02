/* Custom-column value picker for the classic book-edit page.
 *
 * The list button (.btn-cc-picker, rendered for EVERY text custom column in
 * book_edit.html) opens #ccValueModal (custom_column_modal.html, included in
 * book_edit.html — the modal markup MUST be in this page, else the button has
 * nothing to open). It fetches every stored value of the column from
 * /ajax/get_cc_tree/<id> and renders them as a checklist, then writes the
 * comma-joined selection back into the column's ordinary text input so the
 * existing edit pipeline (edit_cc_data -> edit_cc_data_string) persists it
 * unchanged. There is no save endpoint for this modal.
 *
 * Two modes, one code path:
 *   hierarchical -> indented checklist, indent from the server's `depth`
 *   flat         -> plain one-level list (Dewey 778.3 is ONE entry, never a
 *                   778 parent with a 3 child)
 * The server decides and says which in the response; the client never
 * re-detects, so the picker cannot disagree with the browse tree about which
 * a column is.
 */
$(document).ready(function () {
  var activeColumnId = null;
  var entries = [];          // [{value, depth}] as the server sent them
  var isHierarchical = false;
  var selected = {};        // canonical value -> true

  /* Canonicalise exactly like cps/hierarchy.py's split_path/join_path: each
   * dot-segment is stripped and empty segments collapse, so a stored
   * 'Computers.' or 'Computers..' ticks the 'Computers' node the same way the
   * browse tree counts it. */
  function canonicalize(value) {
    return String(value || '')
      .split('.')
      .map(function (s) { return s.trim(); })
      .filter(Boolean)
      .join('.');
  }

  function matchesFilter(value, filter) {
    return !filter || value.toLowerCase().indexOf(filter) !== -1;
  }

  function renderList() {
    var filter = ($('#ccValueSearch').val() || '').toLowerCase();
    var $container = $('#ccValueList').empty();
    var shown = 0;

    entries.forEach(function (entry) {
      var value = String(entry.value == null ? '' : entry.value);
      if (!value || !matchesFilter(value, filter)) {
        return;
      }
      shown += 1;
      var checked = selected[value] ? ' checked' : '';
      // Only a hierarchical column indents; depth is always 0 for flat.
      var indent = isHierarchical ? (entry.depth || 0) * 20 : 0;
      // Escape for safe interpolation into the label and the value attribute.
      var escaped = $('<div>').text(value).html();
      $container.append(
        $('<div class="checkbox"></div>')
          .css('padding-left', indent + 'px')
          .html(
            '<label><input type="checkbox" class="cc-value-checkbox" value="' +
            escaped + '"' + checked + '> ' + escaped + '</label>'
          )
      );
    });

    $('#ccValueEmpty').prop('hidden', shown !== 0);
    if (shown === 0 && filter) {
      // Nothing matched the filter (the column is not empty) — say so rather
      // than showing an empty scroll area that looks like a failure.
      $container.append(
        $('<div class="text-muted"></div>').text('No values match the filter.')
      );
    }
  }

  /* Read the text input back into `selected`. Called on open (to pick up the
   * saved value) and after adding a node, so neither path loses the other. */
  function readInputIntoSelection() {
    var current = $('#custom_column_' + activeColumnId).val() || '';
    Object.keys(selected).forEach(function (key) { delete selected[key]; });
    current.split(',').forEach(function (part) {
      var value = canonicalize(part);
      if (value) {
        selected[value] = true;
      }
    });
  }

  $('.btn-cc-picker').on('click', function () {
    activeColumnId = $(this).data('column-id');
    var columnName = $(this).data('column-name');
    readInputIntoSelection();

    $('#ccValueModalTitle').text(columnName || 'Values');
    $('#ccValueNew').val('');
    $('#ccValueSearch').val('');

    // Open immediately, then populate: waiting for the fetch before showing
    // made the button look dead whenever the request failed.
    $('#ccValueModal').modal('show');
    $('#ccValueList').html(
      '<div class="text-center" style="padding: 2em 0;"><span class="glyphicon glyphicon-refresh glyphicon-refresh-animate"></span></div>'
    );
    $('#ccValueEmpty').prop('hidden', true);

    // getPath() honours a reverse-proxy mount prefix (same helper every other
    // classic AJAX call uses; "" at the domain root).
    $.getJSON(getPath() + '/ajax/get_cc_tree/' + activeColumnId, function (data) {
      isHierarchical = !!data.hierarchical;
      entries = data.entries || [];
      renderList();
    }).fail(function () {
      entries = [];
      $('#ccValueList').html(
        '<div class="alert alert-danger" style="margin-bottom: 0;">Could not load the values for this column.</div>'
      );
    });
  });

  // Typing in the filter box re-renders the list.
  $('#ccValueSearch').on('input', renderList);

  $('#ccValueAddBtn, #ccValueNew').on('click', function (event) {
    if (event.type === 'click' && event.target.id !== 'ccValueAddBtn') {
      return;
    }
    var raw = $('#ccValueNew').val().trim();
    if (!raw) {
      return;
    }
    var node = canonicalize(raw);
    var known = entries.some(function (e) { return String(e.value) === node; });
    if (!known) {
      // A new value on a hierarchical column is inserted at the depth its
      // own dot count implies, so it lands in the right place in the tree.
      entries.push({
        value: node,
        depth: isHierarchical ? node.split('.').length - 1 : 0
      });
      entries.sort(function (a, b) {
        return String(a.value).toLowerCase().localeCompare(String(b.value).toLowerCase());
      });
    }
    selected[node] = true;
    $('#ccValueNew').val('');
    $('#ccValueSearch').val('');
    renderList();
  });

  // Enter in the new-value box adds it, rather than submitting the whole form.
  $('#ccValueNew').on('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      $('#ccValueAddBtn').trigger('click');
    }
  });

  $('#ccValueApplyBtn').on('click', function () {
    $('.cc-value-checkbox').each(function () {
      if ($(this).is(':checked')) {
        selected[String($(this).val())] = true;
      } else {
        delete selected[String($(this).val())];
      }
    });
    $('#custom_column_' + activeColumnId).val(Object.keys(selected).join(', '));
    $('#ccValueModal').modal('hide');
  });
});