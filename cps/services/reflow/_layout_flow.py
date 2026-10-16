"""The same checked reading-flow edges for review, lexical choice and emission."""
from xml.etree import ElementTree as ET

CONTINUABLE = {'paragraph': 'p', 'quote': 'blockquote'}
NOTICE_CLASSES = {'source-evidence-notice', 'source-check-notice'}


def is_notice(node):
    return bool(set(node.get('class', '').split()) & NOTICE_CLASSES)


def separate(role, group):
    """The exact existing channels that do not interrupt the main flow."""
    if role in ('note', 'furniture', 'source_furniture'):
        return True
    if role == 'source':
        node = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">' + group[0]['html'] + '</root>')[0]
        return node.tag.split('}')[-1] == 'aside' or is_notice(node)
    return False


def edge(groups, last):
    """Return (role, atoms) only at an eligible main-flow edge; never skip barriers."""
    main = []
    for role, group in groups:
        if separate(role, group):
            continue
        main.append((role, group))
    if not main:
        return None
    value = main[-1 if last else 0]
    return value if value[0] in CONTINUABLE else None


def raster_endpoint(fragment, last):
    """An exact source-raster endpoint, ignoring only empty navigation anchors."""
    root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">' + fragment + '</root>')
    material = []
    def visit(node):
        if node.get('class') == 'source-raster':
            material.append(True)
        else:
            if (node.text or '').strip(): material.append(False)
            if node.tag.split('}')[-1] == 'img': material.append(False)
            for child in node:
                visit(child)
                if (child.tail or '').strip(): material.append(False)
    visit(root)
    return bool(material and material[-1 if last else 0])
