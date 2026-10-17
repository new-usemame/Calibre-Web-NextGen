"""Source-bound pixel-reading consent; no credentials, reservations or requests."""
import base64
import hashlib
import io
import json
import math
from functools import lru_cache

from . import extract, vision_reading

VERSION = 'vision-page-1'
MARKERS_PER_PAGE = 16
MAX_IMAGE_PIXELS = 2_500_000


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _versions():
    return dict(version=VERSION, reading_version=vision_reading.VERSION,
                model=vision_reading.MODEL, confidence_protocol='tokens',
                marker_allowance=MARKERS_PER_PAGE, max_image_pixels=MAX_IMAGE_PIXELS,
                request_contract_sha256=_digest([_page_specs(), marker_spec()]))


@lru_cache(maxsize=1)
def _page_specs():
    # Initialization is deliberately avoided: a quote has no cache or ledger.
    import re
    prompt = re.sub(r'For a low-confidence word add its.*?licence to rewrite\.',
        'Return transcription as tokens, in exact order. Each token contains text (one whitespace token) and low_confidence (boolean). Mark uncertain ink true; never omit a readable word because confidence is uncertain.',
        vision_reading.PROMPT, flags=re.S)
    schema = vision_reading.schema()
    fields = schema['properties']['blocks']['items']
    fields['properties'].pop('text'); fields['properties'].pop('low_confidence')
    fields['properties']['tokens'] = {'type':'array','items':{'type':'object',
        'additionalProperties':False,'required':['text','low_confidence'],
        'properties':{'text':{'type':'string'},'low_confidence':{'type':'boolean'}}}}
    fields['required'] = list(fields['properties'])
    retry = prompt + '\nReview this same page after a rejected reading. Recheck every block against its printed pixels, all coordinates and confidence fields, and the exact correspondence of printed note references to note IDs. Preserve all original content; do not infer missing ink.\n'
    return {'primary': (prompt, schema, 20000), 'retry': (retry, schema, 20000)}


def bound(width, height, prompt, schema, output):
    inputs = len((prompt + json.dumps(schema)).encode()) + 1024 + max(1024, 2*math.ceil(width*height/750))
    return (inputs*.1 + output*.5)/1e6


def marker_spec():
    prompt = ('Read only the printed note-reference label in this source crop. '
        'Return its exact digits or reference symbol (*, dagger or double dagger). '
        'Ignore enclosing parentheses, punctuation and surrounding prose. Do not infer the label from context. If the ink cannot be read, '
        'return an empty label and uncertain true.')
    schema = {'type':'object','additionalProperties':False,'required':['label','uncertain'],
              'properties':{'label':{'type':'string'},'uncertain':{'type':'boolean'}}}
    return prompt, schema, 1024


def validate(quote, source_sha256):
    if not isinstance(quote, dict): raise ValueError('A current vision estimate is required')
    body = dict(quote); identity = body.pop('identity', None)
    if (quote.get('source_sha256') != source_sha256 or identity != _digest(body) or
            any(quote.get(k) != v for k, v in _versions().items())):
        raise ValueError('The vision estimate differs from the current source or reader')
    return quote


def measure(doc, *, page_numbers=None, progress=None, should_stop=None, **unused):
    from . import vision_pipeline
    from .source_display import SourceDisplay
    from .pipeline import Progress
    numbers = list(range(len(doc))) if page_numbers is None else list(page_numbers)
    rows=[]; coverage=[]
    for n in numbers:
        if should_stop and should_stop():
            from .model import AttemptCancelled
            raise AttemptCancelled('Vision estimate cancelled')
        native = vision_pipeline.clean_native(doc[n])
        coverage.append(dict(page=n, status='native' if native else 'eligible', reason=''))
        if not native:
            display=SourceDisplay(doc,n)
            pix=display.pixmap(scale=2,max_pixels=MAX_IMAGE_PIXELS)
            image=pix.tobytes('jpg',jpg_quality=90)
            primary=bound(pix.width,pix.height,*_page_specs()['primary'])
            retry=bound(pix.width,pix.height,*_page_specs()['retry'])
            marker=bound(MAX_IMAGE_PIXELS,1,*marker_spec())*MARKERS_PER_PAGE
            # A planning estimate, explicitly separate from the strict reservation ceiling.
            expected=(3000*.1+1500*.5)/1e6
            rows.append(dict(page_index0=n,image_sha256=hashlib.sha256(image).hexdigest(),
                width=pix.width,height=pix.height,primary_bound_usd=primary,retry_bound_usd=retry,
                marker_bound_usd=marker,expected_usd=expected,full_bound_usd=primary+retry+marker,
                proposer_bound_usd=primary,verifier_bound_usd=retry+marker))
        if progress: progress(Progress('vision_estimate',page=n+1,pages=len(doc)))
    sample_starts = sample_origins(doc, should_stop=should_stop)
    quote=dict(**_versions(),source_sha256=extract.document_fingerprint(doc),
        source_context_pages=len(doc),first_body_page=sample_starts['20'],sample_starts=sample_starts,eligible_pages=len(rows),
        limited_pages=0,unsupported_pages=0,no_choice_pages=len(numbers)-len(rows),
        pages=rows,coverage=coverage,kind='reservation_ceiling_not_expected_bill',
        assumes_no_paid_cache_hits=True,confirmed_usd=0,held_usd=0,target_usd=.5,
        provider='openai',service_tier='standard',reasoning='low',max_output_tokens=20000,
        expected_usd=sum(r['expected_usd'] for r in rows))
    for field in ('primary_bound_usd','retry_bound_usd','marker_bound_usd','full_bound_usd',
                  'proposer_bound_usd','verifier_bound_usd'):
        quote[field]=sum(r[field] for r in rows)
    quote['identity']=_digest(quote)
    return quote


class Authorizer:
    """Check every actual paid payload against its consented source and allowance."""
    def __init__(self, quote, source_sha256, page_numbers):
        self.quote=validate(quote,source_sha256)
        self.source_sha256=source_sha256
        self.rows={p['page_index0']:p for p in quote['pages'] if p['page_index0'] in page_numbers}
        self.counts={}

    def authorize(self,payload,context):
        from PIL import Image
        from .unit_recovery import SourceIntegrityError
        try:
            n=context['page_index0']; row=self.rows[n]
            if context['source_sha256']!=self.source_sha256: raise ValueError()
            if set(payload) != {'model', 'messages', 'reasoning', 'max_tokens',
                                'response_format', 'provider', 'usage'}: raise ValueError()
            if payload['usage'] != {'include': True}: raise ValueError()
            if set(payload['messages'][0]) != {'role', 'content'} or payload['messages'][0]['role'] != 'user': raise ValueError()
            content=payload['messages'][0]['content']
            if len(payload['messages'])!=1 or len(content)!=2: raise ValueError()
            if set(content[0]) != {'type', 'text'} or content[0]['type'] != 'text': raise ValueError()
            if set(content[1]) != {'type', 'image_url'} or content[1]['type'] != 'image_url': raise ValueError()
            prompt=content[0]['text']; data=content[1]['image_url']
            if set(data) != {'detail', 'url'}: raise ValueError()
            if data.get('detail')!='original': raise ValueError()
            prefix='data:image/jpeg;base64,'
            if not data['url'].startswith(prefix): raise ValueError()
            image=base64.b64decode(data['url'][len(prefix):],validate=True)
            with Image.open(io.BytesIO(image)) as frame:
                width,height=frame.size; frame.verify()
            if width<=0 or height<=0 or width*height>MAX_IMAGE_PIXELS: raise ValueError()
            image_hash=hashlib.sha256(image).hexdigest()
            if context.get('image_sha256')!=image_hash: raise ValueError()
            form=payload['response_format']
            if set(form) != {'type', 'json_schema'} or form['type'] != 'json_schema': raise ValueError()
            declaration=form['json_schema']
            expected_name='printed_note_marker' if context['operation']=='marker' else 'page_reading'
            if set(declaration) != {'name', 'strict', 'schema'} or declaration['name'] != expected_name or declaration['strict'] is not True: raise ValueError()
            schema=declaration['schema']
            actual=(prompt,schema,payload['max_tokens'])
            if context['operation']=='marker':
                role='marker'; maximum=MARKERS_PER_PAGE; expected=marker_spec()
            else:
                if image_hash!=row['image_sha256']: raise ValueError()
                role=next(k for k,v in _page_specs().items() if actual==v)
                maximum=1; expected=_page_specs()[role]
            if actual!=expected or payload['model']!=vision_reading.MODEL: raise ValueError()
            if payload.get('reasoning')!={'effort':'low'}: raise ValueError()
            if payload.get('provider')!={'order':['openai'],'allow_fallbacks':False,
                    'max_price':{'prompt':.1,'completion':.5,'image':0}}: raise ValueError()
            ceiling=bound(width,height,*expected)
            if context['bound_usd']!=ceiling: raise ValueError()
            count=self.counts.get((n,role),0)
            if count>=maximum: raise ValueError()
            self.counts[n,role]=count+1
            return role
        except (KeyError, ValueError, TypeError, StopIteration, OSError) as error:
            raise SourceIntegrityError('Pixel request differs from the consented source, route or allowance') from error


def sample_origins(doc, *, should_stop=None, count=None):
    """Discover a sample opening within its bounded source-only search context."""
    from . import pipeline
    counts = [count] if count is not None else list(range(1, 61))
    maximum = max(pipeline.sample_context(len(doc), n)[0] for n in counts)
    prepared = pipeline.run(doc, page_numbers=list(range(maximum)),
        recovery_opts={'mode': 'off'}, should_stop=should_stop)
    return {str(n): pipeline.first_body_page(prepared.book,
                before=pipeline.sample_context(len(doc), n)[0]) for n in counts}
