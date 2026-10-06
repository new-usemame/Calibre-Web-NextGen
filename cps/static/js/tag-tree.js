/* SPDX-License-Identifier: GPL-3.0-or-later */
document.querySelectorAll('[data-tag-tree]').forEach(function (tree) {
  tree.addEventListener('click', function (event) {
    var button = event.target.closest('.tag-tree-toggle');
    if (!button || !tree.contains(button)) return;
    var children = button.closest('li').querySelector('.tag-tree-children');
    if (!children) return;
    var expanded = button.getAttribute('aria-expanded') !== 'true';
    children.hidden = !expanded;
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = expanded ? '▾' : '▸';
  });
});
