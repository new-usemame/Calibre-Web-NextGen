"""Independent source-free wrapper endpoint crash guard; no book wording."""
from cps.services.reflow import vision_pipeline as vp


def _mapped_pair():
    left=dict(role='body',continues_next=True,printed_tail='Dr.',printed_tail_box=[72,580,85,590])
    right=dict(role='body',continues_previous=True,printed_head='Harper,',printed_head_box=[72,90,95,100],first_line_x=72,opening_column_left=72,source_regions=[[72,90,405,112]])
    return left,right


def test_wrapper_only_visible_tail_is_unmapped_without_crashing():
    left,right=_mapped_pair()
    left.pop('printed_tail');left.pop('printed_tail_box')
    result=vp._continuation_decision(left,right,[left,right],')','Harper, the ordinary account continues.')
    assert result['status']=='unmapped', 'a closing wrapper alone does not supply a lexical source endpoint or earn continuity from paired flags'


def test_source_proved_title_continuation_still_joins():
    left,right=_mapped_pair()
    result=vp._continuation_decision(left,right,[left,right],'The ordinary account refers to Dr.','Harper, the ordinary account continues.')
    assert result['status']=='joined'


def test_genuinely_finished_source_sentence_stays_separate():
    left,right=_mapped_pair();left['printed_tail']='finished.'
    result=vp._continuation_decision(left,right,[left,right],'The ordinary account is finished.','Harper, the next account begins.')
    assert result['status']=='split' and result['reason']=='terminal_punctuation'
