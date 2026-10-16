"""Lossless wire-only tables. Factories, schemas and source snapshots stay exact."""
import copy
import json
from collections import Counter
from .typed_model import _encoded
LEGACY_VERSION='layout-table-1'
LEGACY_DEFINITION='Expand each $layout_table into objects: row values follow fields, with optional extra-key object last. {$v:index} is the exact value_pool string or rectangle at that index. Expand all source and neighbor context exactly.'
TABLE2_VERSION='layout-table-2'
KEY='$layout_table'
TABLE2_DEFINITION=LEGACY_DEFINITION+' Optional defaults apply to EVERY row as exact additional fields, disjoint from fields; the optional extra-key object may override defaults. An absent key stays absent. Empty fields with defaults are valid.'
VERSION='layout-table-3'
DEFINITION=TABLE2_DEFINITION+' Optional column_values maps a field to its exact value dictionary; that column contains integer indices into that dictionary. Pool entries may also be objects or arrays: expand references and tables recursively. Each pool entry may reference only earlier pool entries.'


def _wire_size(value):
    # The representation is transmitted inside a JSON message string. Include
    # its escaping when choosing dictionaries/pools, not just inner JSON size.
    return len(_encoded(_encoded(value).decode()))-2


def _pack(view,version):
    def visit(value):
        if isinstance(value,dict):
            if KEY in value or '$v' in value:raise ValueError('reserved layout table key in source')
            return {k:visit(v) for k,v in value.items()}
        if not isinstance(value,list):return value
        children=[visit(v) for v in value]
        if not children or not all(isinstance(v,dict) and KEY not in v for v in children):return children
        fields=sorted(set.intersection(*(set(v) for v in children)))
        if not fields:return children
        rows=[[v[k] for k in fields]+([{k:x for k,x in v.items() if k not in fields}] if set(v)-set(fields) else []) for v in children]
        table={KEY:dict(fields=fields,rows=rows)}
        regular=copy.deepcopy(table)
        # Default only a key actually present in EVERY record, with explicit
        # overrides for every exceptional JSON value. Comparing encoded values
        # preserves types (Python equality would conflate false and 0).
        defaults={}
        for field in fields:
            counts=Counter(_encoded(v[field]) for v in children)
            common,count=counts.most_common(1)[0]
            if count<2:continue
            trial=defaults|{field:json.loads(common)}
            varying=[k for k in fields if k not in trial]
            compact_rows=[]
            for v in children:
                extra={k:x for k,x in v.items() if k not in fields or
                       (k in trial and _encoded(x)!=_encoded(trial[k]))}
                compact_rows.append([v[k] for k in varying]+([extra] if extra else []))
            compact={KEY:dict(fields=varying,defaults=trial,rows=compact_rows)}
            if len(_encoded(compact))<len(_encoded(table)):
                table=compact;defaults=trial
        # Repeated categorical values (e.g. mixed paragraph/heading/quote roles)
        # share a typed column dictionary without an object wrapper per cell.
        if version==VERSION:
            alternatives=[]
            for candidate in (regular,table):
                for index,field in enumerate(candidate[KEY]['fields']):
                    values=[];indices={};rows=candidate[KEY]['rows']
                    for row in rows:
                        key=_encoded(row[index])
                        if key not in indices:
                            indices[key]=len(values);values.append(row[index])
                    if len(values)==len(rows):continue
                    # Dictionary order is representation only. Canonical typed
                    # values let differently ordered layouts share the same
                    # vocabulary while every row retains its exact value.
                    values.sort(key=_encoded)
                    indices={_encoded(v):i for i,v in enumerate(values)}
                    trial=copy.deepcopy(candidate)
                    trial[KEY].setdefault('column_values',{})[field]=values
                    for row in trial[KEY]['rows']:row[index]=indices[_encoded(row[index])]
                    if _wire_size(trial)<_wire_size(candidate):candidate=trial
                alternatives.append(candidate)
            table=min(alternatives,key=_wire_size)
            return table if _wire_size(table)<_wire_size(children) else children
        return table if len(_encoded(table))<len(_encoded(children)) else children
    packed=visit(copy.deepcopy(view))
    if version==TABLE2_VERSION:
        counts=Counter()
        def count_old(value):
            if isinstance(value,str) and len(_encoded(value))>20:counts[_encoded(value)]+=1
            elif isinstance(value,list) and len(value)==4 and all(type(v) in (int,float) for v in value):counts[_encoded(value)]+=1
            elif isinstance(value,list):
                for v in value:count_old(v)
            elif isinstance(value,dict):
                for v in value.values():count_old(v)
        count_old(packed)
        keys=[k for k,n in counts.items() if len(k)*(n-1)>16*n+2]
        pool=[json.loads(k) for k in keys];indices={k:i for i,k in enumerate(keys)}
        def share_old(value):
            if isinstance(value,(str,list)):
                key=_encoded(value)
                if key in indices:return {'$v':indices[key]}
                if isinstance(value,list):return [share_old(v) for v in value]
            if isinstance(value,dict):return {k:share_old(v) for k,v in value.items()}
            return value
        return dict(wire_format=TABLE2_VERSION,definition=TABLE2_DEFINITION,value_pool=pool,view=share_old(packed))
    counts=Counter()
    def count(value):
        if isinstance(value,(str,list,dict)):counts[_encoded(value)]+=1
        if isinstance(value,list):
            for v in value:count(v)
        elif isinstance(value,dict):
            for v in value.values():count(v)
    count(packed)
    pool=[];indices={}
    def share(value):
        if not isinstance(value,(str,list,dict)):return value
        key=_encoded(value)
        if key in indices:return {'$v':indices[key]}
        result=([share(v) for v in value] if isinstance(value,list) else
                {k:share(v) for k,v in value.items()} if isinstance(value,dict) else value)
        n=counts[key];index=len(pool)
        if n>1 and (n-1)*_wire_size(result)>n*_wire_size({'$v':index})+1:
            indices[key]=index;pool.append(result)
            return {'$v':index}
        return result
    shared=share(packed)
    result=dict(wire_format=VERSION,definition=DEFINITION,value_pool=pool,view=shared)
    plain=dict(result,value_pool=[],view=packed)
    return result if _wire_size(result)<_wire_size(plain) else plain


def pack(view):
    # Retain the existing capacity guarantee: new dictionaries/pools can only
    # improve the escaped wire size. The enclosing route identity still changes.
    current=_pack(view,VERSION);previous=_pack(view,TABLE2_VERSION)
    return current if _wire_size(current)<_wire_size(previous) else previous


def unpack(packed):
    if (not isinstance(packed,dict) or set(packed)!= {'wire_format','definition','value_pool','view'}
            or (packed['wire_format'],packed['definition']) not in
                ((VERSION,DEFINITION),(TABLE2_VERSION,TABLE2_DEFINITION),(LEGACY_VERSION,LEGACY_DEFINITION))):
        raise ValueError('invalid layout table envelope')
    pool=packed['value_pool']
    if not isinstance(pool,list) or not all(isinstance(row,(str,list,dict)) if packed['wire_format']==VERSION else
            isinstance(row,str) or (isinstance(row,list) and len(row)==4 and all(type(v) in (int,float) for v in row)) for row in pool):
        raise ValueError('invalid layout value pool')
    decoded_pool=[]
    def visit(value,limit=None):
        if isinstance(value,list):return [visit(v,limit) for v in value]
        if not isinstance(value,dict):return value
        if '$v' in value:
            if (set(value)!={'$v'} or type(value['$v']) is not int or not 0<=value['$v']<len(pool)
                    or (limit is not None and value['$v']>=limit)):raise ValueError('invalid layout value reference')
            return (copy.deepcopy(decoded_pool[value['$v']]) if packed['wire_format']==VERSION
                    else copy.deepcopy(pool[value['$v']]))
        if KEY not in value:return {k:visit(v,limit) for k,v in value.items()}
        if set(value)!={KEY}:raise ValueError('invalid layout table')
        table=visit(value[KEY],limit)
        if (not isinstance(table,dict) or not {'fields','rows'}<=set(table)
                or set(table)-{'fields','rows','defaults','column_values'}):
            raise ValueError('invalid layout table')
        if 'defaults' in table and packed['wire_format']==LEGACY_VERSION:
            raise ValueError('defaults require layout table version 2')
        if 'column_values' in table and packed['wire_format']!=VERSION:
            raise ValueError('column dictionaries require layout table version 3')
        fields,rows=table['fields'],table['rows']
        defaults=table.get('defaults',{})
        if (not isinstance(fields,list) or not isinstance(defaults,dict) or not (fields or defaults)
                or not all(isinstance(k,str) for k in fields) or len(set(fields))!=len(fields)
                or set(fields)&set(defaults) or not isinstance(rows,list)):
            raise ValueError('invalid layout table fields')
        columns=table.get('column_values',{})
        if (not isinstance(columns,dict) or set(columns)-set(fields)
                or not all(isinstance(v,list) and v for v in columns.values())):
            raise ValueError('invalid layout column dictionary')
        result=[]
        for row in rows:
            if not isinstance(row,list) or len(row) not in (len(fields),len(fields)+1) or (len(row)>len(fields) and (not isinstance(row[-1],dict) or set(row[-1])&set(fields))):
                raise ValueError('invalid layout table row')
            record=copy.deepcopy(defaults)
            for k,v in zip(fields,row[:len(fields)]):
                if k in columns:
                    if type(v) is not int or not 0<=v<len(columns[k]):raise ValueError('invalid layout column reference')
                    v=copy.deepcopy(columns[k][v])
                record[k]=v
            if len(row)>len(fields):record.update(row[-1])
            result.append(record)
        return result
    if packed['wire_format']==VERSION:
        # Validate even unused entries. Backward-only references make expansion
        # acyclic; decoding once also avoids repeatedly expanding shared trees.
        for index,entry in enumerate(pool):decoded_pool.append(visit(entry,index))
    return visit(packed['view'])


def compact_message(message):
    if message.get('role')!='user':return
    try:view=json.loads(message['content'])
    except (TypeError,ValueError):return
    if isinstance(view,dict) and view.get('wire_format') in (VERSION,TABLE2_VERSION,LEGACY_VERSION):
        unpack(view) # Refuse a damaged representation before hashing/reserving it.
        return
    packed=pack(view)
    if len(_encoded(packed))<len(message['content'].encode()):
        if _encoded(unpack(packed))!=_encoded(view):
            raise ValueError('layout table changed logical source')
        message['content']=_encoded(packed).decode()


def source_view(messages):
    """Read the exact logical final view, including historical atom-column wires."""
    try:view=json.loads(messages[-1]['content'])
    except (ValueError,TypeError):return None
    if isinstance(view,dict) and view.get('wire_format') in (VERSION,TABLE2_VERSION,LEGACY_VERSION):return unpack(view)
    if isinstance(view,dict):
        for group in [view,view.get('previous')]:
            if isinstance(group,dict) and 'atom_fields' in group:
                fields=group.pop('atom_fields');extras=group.pop('atom_extras',{})
                group['atoms']=[dict(zip(fields,row))|extras.get(row[0],{}) for row in group['atoms']]
    return view
