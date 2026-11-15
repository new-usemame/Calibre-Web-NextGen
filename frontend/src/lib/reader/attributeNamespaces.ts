/**
 * srcdoc parses EPUB XHTML as HTML, preserving `epub:type` spelling but losing
 * its namespace. Restore declared attribute namespaces without replacing the
 * existing literal attributes: both namespaced publisher selectors and HTML
 * escaped-colon workarounds remain usable. Element structure is unchanged, so
 * stored CFIs keep the same paths (including HTML's implied table sections).
 */
export function restoreBookAttributeNamespaces(root: Element): void {
  const visit = (element: Element, inherited: ReadonlyMap<string, string>) => {
    let namespaces = inherited;
    const attributes = Array.from(element.attributes);
    const declarations = attributes.filter(attribute => attribute.name.startsWith('xmlns:'));
    if (declarations.length) {
      const local = new Map(inherited);
      for (const declaration of declarations) {
        local.set(declaration.name.slice(6), declaration.value);
      }
      namespaces = local;
    }
    for (const attribute of attributes) {
      if (attribute.namespaceURI || attribute.name.startsWith('xmlns:')) continue;
      const colon = attribute.name.indexOf(':');
      if (colon < 1) continue;
      const namespace = namespaces.get(attribute.name.slice(0, colon));
      if (namespace) element.setAttributeNS(namespace, attribute.name, attribute.value);
    }
    for (const child of element.children) visit(child, namespaces);
  };
  visit(root, new Map([['xml', 'http://www.w3.org/XML/1998/namespace']]));
}
