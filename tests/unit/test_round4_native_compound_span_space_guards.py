"""Independent source-free merge guards; no checker, source, or production changes."""
import pytest
from xml.etree import ElementTree as ET
from cps.services.reflow import build_epub

LEFT='<p><span data-source-page="0">An unusually deep-</span></p>'
PAGE='<span id="pg_0001" class="reflow-page"></span>'
RETURN='<a class="pdf-return" href="#pdfref_p0000_x47" aria-label="Return to PDF reference" title="Return to PDF reference">Return</a>'
def visible(fragment):
 return ''.join(ET.fromstring(fragment).itertext())
def nav_guard(fragment):
 root=ET.fromstring(fragment)
 assert {x.get('data-source-page') for x in root.iter() if x.get('data-source-page')}=={'0','1'}
 assert len([x for x in root.iter() if x.get('id')=='pg_0001'])==1
 assert len([x for x in root.iter() if x.get('id')=='pdfgoto_p0000_x47'])==1
 assert len([x for x in root.iter() if x.get('href')=='#pdfref_p0000_x47' and x.text=='Return'])==1

@pytest.mark.parametrize('earned',[False,True])
def test_nested_transparent_source_head_space(earned):
 head='<p><span data-source-page="1"><em><strong><a id="pdfgoto_p0000_x47" href="#destination"> \t seated</a></strong></em> root continues.'+RETURN+'</span></p>'
 result=build_epub.merge_paragraphs(LEFT,head,PAGE,words={'deepseated'} if earned else set())
 nav_guard(result)
 assert ('deepseated' if earned else 'deep-seated') in visible(result)
 assert '<em><strong><a id="pdfgoto_p0000_x47" href="#destination">seated</a></strong></em>' in result

def test_reading_copy_before_after_removed_source_space_keeps_order():
 a='<span class="source-reading-annotation" id="reading-return-reading-'+('b'*64)+'">Before source check.</span>'
 b='<span class="source-reading-annotation" id="reading-return-reading-'+('c'*64)+'">After source check.</span>'
 head='<p><span data-source-page="1">'+a+' \t '+b+'<a id="pdfgoto_p0000_x47"><em>seated</em></a> root continues.'+RETURN+'</span></p>'
 result=build_epub.merge_paragraphs(LEFT,head,PAGE,words=set())
 nav_guard(result)
 assert 'deep-seated' in visible(build_epub._READING_COPY.sub('',result))
 assert result.count(a)==result.count(b)==1 and result.index(a)<result.index(b)<result.index('<em>seated</em>')
 assert a in result and b in result and RETURN in result

@pytest.mark.parametrize('prefix',['<img src="authored.jpg" alt="authored glyph"/>','<br/>','<sup>7</sup>'])
def test_visible_prefix_stops_transparent_head_space_removal(prefix):
 head='<span data-source-page="1">'+prefix+' seated continues.</span>'
 result=build_epub.merge_paragraphs(LEFT,'<p>'+head+'</p>',PAGE,words=set())
 assert head in result,'Visible source prefix and subsequent spacing remain byte exact'

def test_ordinary_sentence_spacing_and_provenance_remain():
 left='<p><span data-source-page="0">The root continues</span></p>'
 head='<span data-source-page="1"><em>  through ordinary soil.</em></span>'
 result=build_epub.merge_paragraphs(left,'<p>'+head+'</p>',PAGE,words=set())
 assert head in result and ' '.join(visible(result).split())=='The root continues through ordinary soil.'
 assert {x.get('data-source-page') for x in ET.fromstring(result).iter() if x.get('data-source-page')}=={'0','1'}

def test_punctuation_dash_does_not_trim_inner_source_spacing():
 left='<p><span data-source-page="0">The explanation ends—</span></p>'
 head='<span data-source-page="1"><em> a different clause follows.</em></span>'
 result=build_epub.merge_paragraphs(left,'<p>'+head+'</p>',PAGE,words=set())
 assert head in result and 'ends— a different' in visible(result)
