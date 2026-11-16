/* The classic edit picker fills the existing input; the normal Save persists it. */
$(document).ready(function () {
  var activeColumnId = null;
  var entries = [];
  var isHierarchical = false;
  var multiple = false;
  var selected = new Set();
  var pending = null;
  var generation = 0;
  var ready = false;
  var $modal = $('#ccValueModal');

  function renderList() {
    if (!ready) return;
    var filter = ($('#ccValueSearch').val() || '').toLowerCase();
    var $container = $('#ccValueList').empty();
    var shown = 0;
    entries.forEach(function (entry) {
      var value = entry.value;
      if (!value || (filter && value.toLowerCase().indexOf(filter) === -1)) return;
      shown += 1;
      // Values are text and DOM properties, never interpolated HTML attributes.
      var $input = $('<input>', { type: 'checkbox', class: 'cc-value-checkbox' })
        .val(value).prop('checked', selected.has(value));
      $('<div class="checkbox"></div>')
        .css('padding-left', (isHierarchical ? entry.depth * 20 : 0) + 'px')
        .append($('<label>').append($input).append(document.createTextNode(' ' + value)))
        .appendTo($container);
    });
    $('#ccValueEmpty').prop('hidden', shown !== 0 || !!filter);
    if (!shown && filter) {
      $container.append($('<div class="text-muted">').text($modal.data('no-match')));
    }
  }

  function readInputIntoSelection() {
    var value = $('#custom_column_' + activeColumnId).val() || '';
    selected = new Set((multiple ? value.split(',') : [value])
      .map(function (part) { return part.trim(); }).filter(Boolean));
  }

  function loadValues() {
    var currentGeneration = ++generation;
    if (pending) pending.abort();
    ready = false;
    entries = [];
    $('#ccValueApplyBtn, #ccValueAddBtn').prop('disabled', true);
    $('#ccValueEmpty, #ccValueInvalid').prop('hidden', true);
    $('#ccValueList').text($modal.data('loading'));
    pending = $.getJSON(getPath() + '/ajax/get_cc_tree/' + activeColumnId, function (data) {
      if (currentGeneration !== generation) return;
      isHierarchical = !!data.hierarchical;
      multiple = !!data.is_multiple;
      readInputIntoSelection();
      entries = data.entries || [];
      selected.forEach(function (value) {
        if (!entries.some(function (entry) { return entry.value === value; })) {
          entries.push({ value: value, depth: 0 });
        }
      });
      ready = true;
      $('#ccValueApplyBtn, #ccValueAddBtn').prop('disabled', false);
      renderList();
    }).fail(function (_, status) {
      if (currentGeneration !== generation || status === 'abort') return;
      $('#ccValueList').empty().append($('<div class="alert alert-danger">')
        .text($modal.data('load-error'))).append($('<button>', { type: 'button', class: 'btn btn-default' })
        .text($modal.data('retry')).on('click', loadValues));
    });
  }

  $('.btn-cc-picker').on('click', function () {
    activeColumnId = $(this).data('column-id');
    $('#ccValueModalTitle').text($(this).data('column-name'));
    $('#ccValueNew, #ccValueSearch').val('');
    $modal.modal('show');
    loadValues();
  });

  $modal.on('hidden.bs.modal', function () {
    generation += 1;
    if (pending) pending.abort();
  });
  $('#ccValueSearch').on('input', renderList);
  // Update the selection before filtering or adding can rebuild the DOM.
  $('#ccValueList').on('change', '.cc-value-checkbox', function () {
    if (this.checked) {
      if (!multiple) selected.clear();
      selected.add(this.value);
    } else selected.delete(this.value);
    renderList();
  });

  $('#ccValueAddBtn').on('click', function () {
    if (!ready) return;
    var raw = $('#ccValueNew').val().trim();
    if (multiple && raw.indexOf(',') !== -1) {
      $('#ccValueInvalid').prop('hidden', false);
      return;
    }
    var value = isHierarchical ? raw.split('.').map(function (s) { return s.trim(); })
      .filter(Boolean).join('.') : raw;
    if (!value) return;
    if (!entries.some(function (entry) { return entry.value === value; })) {
      entries.push({ value: value, depth: isHierarchical ? value.split('.').length - 1 : 0 });
    }
    if (!multiple) selected.clear();
    selected.add(value);
    $('#ccValueNew, #ccValueSearch').val('');
    $('#ccValueInvalid').prop('hidden', true);
    renderList();
  });
  $('#ccValueNew').on('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      $('#ccValueAddBtn').trigger('click');
    }
  });
  $('#ccValueApplyBtn').on('click', function () {
    if (!ready) return;
    $('#custom_column_' + activeColumnId).val(Array.from(selected).join(', ')).trigger('input').trigger('change');
    $modal.modal('hide');
  });
});
