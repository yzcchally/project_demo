"""A real native loading modal cannot grant authority to the page behind it."""
import xml.etree.ElementTree as ET

import pytest

from autoglm.loading import loading_scene
from autoglm.protocol import Action
from autoglm.safety import GroundingError, _candidates, resolve_action
from device.amap import ID, PACKAGE


REQUEST = {'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市',
           'preference':'fewer_transfers','use_current_location':False}


def node(cls='android.view.ViewGroup',rect='[322,1294][878,1499]',**attrs):
    return ET.Element('node',{'package':PACKAGE,'class':cls,'bounds':rect,'text':'','content-desc':'',
                             'resource-id':'','clickable':'false','enabled':'true','visible-to-user':'true',**attrs})


def dialog():
    view=node('android.widget.LinearLayout',**{'resource-id':ID+'loading_view'})
    root=node(**{'resource-id':ID+'id_root'})
    spinner=node('android.widget.ImageView','[362,1350][455,1443]',**{'resource-id':ID+'lottie_view'})
    text=node('android.widget.TextView','[495,1366][712,1428]',text='正在加载...',**{'resource-id':ID+'loading_text'})
    cancel=node('android.widget.ImageView','[752,1354][838,1440]',clickable='true',**{'resource-id':ID+'close'})
    root.extend([spinner,text,cancel]);view.append(root)
    window=node('android.widget.FrameLayout');window.append(view)
    return window,cancel,root,text


def frame(*roots):
    hierarchy=ET.Element('hierarchy',{'window-id':'current-loading-window'})
    hierarchy.extend(roots)
    return {'nodes':list(hierarchy.iter('node')),'xml':ET.tostring(hierarchy,encoding='unicode'),
            'width':1200,'height':2640,'screen_width':1200,'screen_height':2640,'rotation':0,
            'current_package':PACKAGE,'current_app':PACKAGE,'epoch':0,
            'evidence':{'evidence_id':'current','observation_id':'current'}}


def query_page():
    page=node('android.widget.FrameLayout','[0,0][1200,2640]')
    field=node('android.widget.EditText','[100,100][1100,250]',focused='true',clickable='true',
               **{'resource-id':ID+'search_input'})
    page.extend([field,node('android.widget.TextView','[752,1354][838,1440]',text='付款',clickable='true')])
    return page


def test_loading_allows_only_real_cancel_wait_or_back_without_a_success_claim():
    window,cancel,_,_=dialog();shot=frame(window)
    grounded=resolve_action(Action('Tap',{'element':[661,528]}),shot,REQUEST)
    assert grounded.node is cancel and grounded.point==(793,1393)
    assert grounded.role=='loading_cancel' and grounded.page=='loading' and grounded.binding=={'loading_cancel':True}
    assert resolve_action(Action('Wait',{'duration':.25}),shot,REQUEST).duration==.25
    assert resolve_action(Action('Back',{}),shot,REQUEST).page=='loading'
    assert resolve_action(Action('Help',{'message':'需要协助'}),shot,REQUEST).kind=='Help'
    candidates=_candidates(shot,REQUEST,{})
    assert len(candidates)==1 and candidates[0].node is cancel


@pytest.mark.parametrize('action',[Action('Type',{'text':REQUEST['origin']}),Action('Finish',{'message':'已完成'}),
                                  Action('Launch',{'app':'高德地图'}),
                                  Action('Swipe',{'start':[500,700],'end':[500,300]}),
                                  Action('Tap',{'element':[320,528]}),Action('Tap',{'element':[500,85]})])
def test_loading_wrong_action_has_only_the_verified_modal_redecision_code(action):
    window,_,_,_=dialog();shot=frame(query_page(),window)
    with pytest.raises(GroundingError) as rejected:resolve_action(action,shot,REQUEST)
    assert rejected.value.code=='loading_overlay_active'


def test_verified_cancel_ignores_only_the_proven_earlier_native_query_page():
    window,cancel,_,_=dialog();shot=frame(query_page(),window)
    assert resolve_action(Action('Tap',{'element':[661,528]}),shot,REQUEST).node is cancel
    assert [item.node for item in _candidates(shot,REQUEST,{})]==[cancel]


@pytest.mark.parametrize('overlay',['later_inert','earlier_small','foreign','later_full_page','same_modal_parent'])
def test_loading_cancel_rejects_unproven_covering_surfaces(overlay):
    window,_,_,_=dialog()
    cover=node('android.widget.ImageView','[752,1354][838,1440]')
    if overlay=='later_inert':shot=frame(window,cover)
    elif overlay=='earlier_small':shot=frame(cover,window)
    elif overlay=='foreign':cover.set('package','com.example.other');shot=frame(window,cover)
    elif overlay=='later_full_page':shot=frame(window,query_page())
    else:window.insert(0,cover);shot=frame(window)
    assert loading_scene(shot) is not None
    with pytest.raises(GroundingError) as rejected:resolve_action(Action('Tap',{'element':[661,528]}),shot,REQUEST)
    assert rejected.value.code=='grounding_rejected'


@pytest.mark.parametrize('flag',['enabled','visible-to-user'])
def test_loading_cancel_does_not_treat_hidden_or_disabled_nodes_as_current_covers(flag):
    window,cancel,_,_=dialog();cover=node('android.widget.ImageView','[752,1354][838,1440]',**{flag:'false'})
    assert resolve_action(Action('Tap',{'element':[661,528]}),frame(window,cover),REQUEST).node is cancel


@pytest.mark.parametrize('mutation',['unknown_child','wrong_caption','wrong_class','duplicate_close','hidden_cancel'])
def test_unverified_dialog_does_not_authorize_other_close_icons_or_underlying_inputs(mutation):
    window,cancel,root,text=dialog()
    if mutation=='unknown_child':root.append(node('android.widget.ImageView','[752,1354][838,1440]',clickable='true'))
    elif mutation=='wrong_caption':text.set('text','请登录')
    elif mutation=='wrong_class':cancel.set('class','android.widget.Button')
    elif mutation=='duplicate_close':root.append(node('android.widget.ImageView','[700,1354][750,1440]',clickable='true',**{'resource-id':ID+'close'}))
    else:cancel.set('visible-to-user','false')
    shot=frame(query_page(),window)
    assert loading_scene(shot) is None and _candidates(shot,REQUEST,{})==[]
    for action in (Action('Tap',{'element':[661,528]}),Action('Type',{'text':REQUEST['origin']})):
        with pytest.raises(GroundingError) as rejected:resolve_action(action,shot,REQUEST)
        assert rejected.value.code=='grounding_rejected'
