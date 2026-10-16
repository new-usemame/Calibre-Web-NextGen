"""Checked strong ranges. Source, never rendered output, supplies expectations."""
from xml.etree import ElementTree as ET
from . import _layout_atoms as atoms
from ._layout_notes import tree


def eligible(source):
    opaque={b['range'][0] for b in source['blocks'] if b['opaque']}
    return [a['id'] for a in source['atoms'] if not a['protected']
            and a['id'] not in opaque and not len(tree(a['html']))]


def eligible_ranges(source):
    """Losslessly describe runs of eligible source IDs without repeating each ID."""
    allowed=set(eligible(source));runs=[];previous=False
    for atom in source['atoms']:
        if atom['id'] in allowed:
            if previous:runs[-1][1]=atom['id']
            else:runs.append([atom['id'],atom['id']])
            previous=True
        else:previous=False
    return runs


def checked(source, answer, groups):
    operations=answer.get('emphasis', [])
    atoms.need(isinstance(operations, list), 'emphasis ranges')
    ids=[a['id'] for a in source['atoms']]
    allowed=set(eligible(source))
    forbidden=set(source.get('wrap_lefts', []))
    for join in answer['joins']:
        forbidden.update((join['left'],join['right']))
    if answer.get('boundary_join'):
        forbidden.add(answer['boundary_join']['right'])
    from . import _layout_notes
    for binding in _layout_notes.checked(source, answer, groups):
        forbidden.add(binding.get('atom',binding['reference']))
    existing={n.get('id') for a in source['atoms'] for n in tree(a['html']).iter() if n.get('id')}
    seen=set();result=[]
    for pair in operations:
        atoms.need(isinstance(pair,list) and len(pair)==2 and all(isinstance(v,str) and v in ids for v in pair), 'emphasis endpoints')
        start,end=map(ids.index,pair)
        atoms.need(start<=end, 'reversed emphasis')
        owned=ids[start:end+1]
        atoms.need(set(owned)<=allowed and not set(owned)&(forbidden|seen), 'unsupported or overlapping emphasis')
        matches=[]
        for index,(role,group) in enumerate(groups):
            order=[a['id'] for a in group]
            if owned[0] in order:
                offset=order.index(owned[0])
                if role in ('paragraph','quote') and order[offset:offset+len(owned)]==owned:
                    matches.append(index)
        atoms.need(len(matches)==1, 'emphasis crosses prose group or source order')
        identity='emphasis-'+atoms.digest([source['snapshot'],pair])
        atoms.need(identity not in existing, 'emphasis identity collision')
        existing.add(identity);seen.update(owned)
        result.append(dict(id=identity,group=matches[0],first=owned[0],last=owned[-1],ids=owned))
    return result


def verified_source_output(source, answer, groups, rendered):
    selected=checked(source,answer,groups)
    if not selected:
        return rendered
    root=tree(rendered['body'])
    atoms.need(not (root.text or '').strip() and all(not (n.tail or '').strip() for n in root), 'emphasis output outside groups')
    body=[i for i,(role,_) in enumerate(groups) if role not in ('furniture','source_furniture')]
    atoms.need(len(root)==len(body), 'emphasis output groups changed')
    joins={j['left']:j for j in answer['joins']}
    def exact(group, stop=None):
        parts=[]
        for i,a in enumerate(group):
            if a['id']==stop:break
            value=a['text'][:-1] if a['id'] in joins and joins[a['id']]['hyphen']=='drop' else a['text']
            parts.append(value)
            if i+1<len(group) and a['id'] not in joins:parts.append(' ')
        return ''.join(parts)
    removals=[]
    for selection in selected:
        parent=root[body.index(selection['group'])]
        found=[n for n in root.iter() if n.get('id')==selection['id']]
        atoms.need(len(found)==1 and found[0] in list(parent), 'emphasis moved or missing')
        node=found[0]
        group=groups[selection['group']][1]
        expected=' '.join(a['text'] for a in group if a['id'] in selection['ids'])
        atoms.need(node.tag=='strong' and node.attrib=={'id':selection['id']} and not len(node) and node.text==expected, 'emphasis wrapper or text changed')
        prefix=parent.text or ''
        for child in parent:
            if child is node:break
            prefix+=''.join(child.itertext())+(child.tail or '')
        atoms.need(prefix==exact(group,selection['first']), 'emphasis source position changed')
        atoms.need(''.join(parent.itertext())==exact(group), 'emphasis group text or spacing changed')
        removals.append((parent,node))
    for parent,node in removals:
        index=list(parent).index(node);value=(node.text or '')+(node.tail or '')
        if index:parent[index-1].tail=(parent[index-1].tail or '')+value
        else:parent.text=(parent.text or '')+value
        parent.remove(node)
    return dict(rendered,body=''.join(ET.tostring(n,encoding='unicode') for n in root))
