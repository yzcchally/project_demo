"""Decoration exceptions keep every other pixel and native fact exact."""
import base64
import copy
import io
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw
import pytest

from autoglm.executor import proposal_is_current
from autoglm.freshness import status_bar_frame
from autoglm.protocol import Action


W,H=400,800
BAR=[0,0,W,28]
FIELD=[60,60,350,160]
CARET=[65,85,69,125]
BLUE=(66,135,255)


def encoded(image):
    out=io.BytesIO();image.save(out,format='PNG')
    return base64.b64encode(out.getvalue()).decode()


def snapshot(*,caret=False,clock=0):
    image=Image.new('RGB',(W,H),'white');draw=ImageDraw.Draw(image)
    if clock:draw.rectangle([310,8,320,16],fill='black')
    if caret:draw.rectangle([CARET[0],CARET[1],CARET[2]-1,CARET[3]-1],fill=BLUE)
    root=ET.Element('hierarchy',{'window-id':'window-current','nonce':'random','captured-at':'now'})
    ET.SubElement(root,'node',{'package':'com.autonavi.minimap','class':'android.widget.EditText',
        'resource-id':'com.autonavi.minimap:id/search_edit','bounds':'[60,60][350,160]',
        'text':'','focused':'true','enabled':'true','visible-to-user':'true','editable':'true'})
    return {'epoch':1,'width':W,'height':H,'rotation':0,'current_app':'com.autonavi.minimap',
            'xml':ET.tostring(root,encoding='unicode'),'screenshot_base64':encoded(image),
            'status_bar_frame':BAR.copy(),
            'caret_context':{'role':'query','resource_id':'com.autonavi.minimap:id/search_edit','bounds':FIELD.copy()}}


def changed_pixels(shot,box,color):
    result=copy.deepcopy(shot)
    image=Image.open(io.BytesIO(base64.b64decode(shot['screenshot_base64']))).convert('RGB')
    ImageDraw.Draw(image).rectangle(box,fill=color)
    result['screenshot_base64']=encoded(image)
    return result


def test_only_os_verified_small_status_bar_change_is_ignored():
    before,after=snapshot(),snapshot(clock=1)
    assert proposal_is_current(Action('Tap',{'element':[500,100]}),before,after)


def test_uniform_caret_blink_inside_the_same_proven_focused_query_field_is_ignored():
    assert proposal_is_current(Action('Type',{'text':'体育西路'}),snapshot(),snapshot(caret=True))
    assert proposal_is_current(Action('Tap',{'element':[500,100]}),snapshot(caret=True),snapshot())


def test_route_editor_caret_can_occupy_most_of_its_small_native_field():
    before,after=snapshot(),snapshot()
    # Actual route editor typography: a 74px caret in a 98px input field.
    for shot in (before,after):
        shot['xml']=shot['xml'].replace('[60,60][350,160]','[60,60][350,158]')
        shot['caret_context']['bounds']=[60,60,350,158]
    after=changed_pixels(after,(65,70,71,143),BLUE)
    assert proposal_is_current(Action('Type',{'text':'体育西路'}),before,after)


def test_caret_and_status_change_together_do_not_mask_any_other_pixel():
    assert proposal_is_current(Action('Type',{'text':'体育西路'}),snapshot(),snapshot(caret=True,clock=1))
    after=changed_pixels(snapshot(caret=True,clock=1),(200,500,201,501),'red')
    assert not proposal_is_current(Action('Type',{'text':'体育西路'}),snapshot(),after)


@pytest.mark.parametrize('params',[{'element':[800,10]}, {'element':[500,10]}])
def test_model_tap_in_ignored_status_bar_is_not_permitted(params):
    assert not proposal_is_current(Action('Tap',params),snapshot(),snapshot(clock=1))


def test_swipe_cannot_target_or_cross_the_ignored_status_bar():
    action=Action('Swipe',{'start':[500,10],'end':[500,800]})
    assert not proposal_is_current(action,snapshot(),snapshot(clock=1))


@pytest.mark.parametrize('field,value',[
    ('epoch',2),('rotation',1),('width',401),('height',801),('current_app','com.example.demo'),
])
def test_global_changes_never_receive_decoration_exceptions(field,value):
    before,after=snapshot(),snapshot(caret=True,clock=1);after[field]=value
    assert not proposal_is_current(Action('Type',{'text':'体育西路'}),before,after)


@pytest.mark.parametrize('replacement',[
    ('text=""','text="另一个地点"'),('window-current','window-other'),
    ('focused="true"','focused="false"'),('bounds="[60,60][350,160]"','bounds="[65,60][355,160]"'),
])
def test_native_text_focus_window_or_bounds_changes_are_still_rejected(replacement):
    before,after=snapshot(),snapshot(caret=True)
    after['xml']=after['xml'].replace(*replacement)
    assert not proposal_is_current(Action('Type',{'text':'体育西路'}),before,after)


@pytest.mark.parametrize('box,color',[
    ((60,65,160,100),'lightblue'),  # text/selection/overlay in the field
    ((65,85,68,124),'black'),      # a changed glyph, not an accent caret
    ((180,300,183,339),BLUE),      # an unrelated vertical overlay outside field
    ((65,85,105,89),BLUE),         # horizontal UI, not a caret
    ((10,650,20,660),'gray'),      # keyboard animation
    ((20,300,200,500),'white'),    # identical region (paired with marker below)
])
def test_unknown_visual_changes_are_not_ignored(box,color):
    before=snapshot()
    after=changed_pixels(before,box,color)
    if box==(20,300,200,500):
        after=changed_pixels(after,(20,300,21,301),'gray')
    assert not proposal_is_current(Action('Type',{'text':'体育西路'}),before,after)


@pytest.mark.parametrize('mutation',['missing_context','missing_os_frame','unknown_role','moved_os_frame','hidden_ancestor'])
def test_missing_or_changed_independent_decoration_proofs_fail_closed(mutation):
    before,after=snapshot(),snapshot(caret=True)
    if mutation=='missing_context':after.pop('caret_context')
    elif mutation=='missing_os_frame':after.pop('status_bar_frame')
    elif mutation=='unknown_role':after['caret_context']['role']='model-claimed-role'
    elif mutation=='moved_os_frame':after['status_bar_frame']=[0,0,W,29]
    else:after['xml']=after['xml'].replace('visible-to-user="true"','visible-to-user="false"')
    assert not proposal_is_current(Action('Type',{'text':'体育西路'}),before,after)


def test_status_bar_frame_comes_only_from_explicit_os_insets_not_window_or_text_bounds():
    line='InsetsSource: {mType=ITYPE_STATUS_BAR mFrame=[0,0][1200,154] mVisible=true}'
    assert status_bar_frame(line,1200,2640)==[0,0,1200,154]
    assert status_bar_frame('InsetsSource type=ITYPE_STATUS_BAR frame=[0,0][400,28] visible=true',400,800)==BAR
    for bad in [line.replace('true','false'),line.replace('154','600'),line.replace('1200','1000'),
                line.replace('ITYPE_STATUS_BAR','ITYPE_IME'),line.replace('mVisible=true',''),
                line+'\n'+line.replace('154','155')]:
        assert status_bar_frame(bad,1200,2640) is None


def test_real_android_source_control_lines_do_not_invalidate_the_actual_status_source():
    dump='''InsetsSource type=ITYPE_STATUS_BAR frame=[0,0][1200,154] visible=true
    ITYPE_STATUS_BAR -> Window{native application}
    mSource=InsetsSource type=ITYPE_STATUS_BAR frame=[0,0][1200,154] visible=true
    mControl=InsetsSourceControl type=ITYPE_STATUS_BAR mLeash=Surface(native) mInsetsHint=Insets{top=154}'''
    assert status_bar_frame(dump,1200,2640)==[0,0,1200,154]
    assert status_bar_frame(dump+'\nInsetsSource type=ITYPE_STATUS_BAR frame=[0,0][1200,400] visible=true',1200,2640) is None


def loading_snapshot(*,frame=0,clock=0):
    shot=snapshot(clock=clock)
    root=ET.Element('hierarchy',{'window-id':'loading-window'})
    def node(parent,key,cls,box,**attrs):
        return ET.SubElement(parent,'node',{'package':'com.autonavi.minimap',
            'resource-id':'com.autonavi.minimap:id/'+key,'class':cls,
            'bounds':f'[{box[0]},{box[1]}][{box[2]},{box[3]}]',
            'enabled':'true','visible-to-user':'true','clickable':'false',**attrs})
    view=node(root,'loading_view','android.widget.LinearLayout',[90,320,310,420])
    body=node(view,'id_root','android.view.ViewGroup',[90,320,310,420])
    node(body,'lottie_view','android.widget.ImageView',[105,350,137,382])
    node(body,'loading_text','android.widget.TextView',[145,350,245,382],text='正在加载...')
    node(body,'close','android.widget.ImageView',[260,350,300,390],clickable='true')
    shot['xml']=ET.tostring(root,encoding='unicode')
    shot.pop('caret_context',None)
    return changed_pixels(shot,(110+frame,355,112+frame,375),'blue')


@pytest.mark.parametrize('action',[Action('Back',{}),Action('Tap',{'element':[700,460]})])
def test_same_native_loading_spinner_can_animate_while_dismissing_its_dialog(action):
    assert proposal_is_current(action,loading_snapshot(),loading_snapshot(frame=4))
    assert proposal_is_current(action,loading_snapshot(),loading_snapshot(frame=4,clock=1))


@pytest.mark.parametrize('action',[
    Action('Tap',{'element':[100,100]}),  # underlying map
    Action('Tap',{'element':[300,460]}),  # spinner is not a cancel target
    Action('Type',{'text':'体育西路'}),Action('Finish',{'message':'完成'}),
    Action('Swipe',{'start':[700,460],'end':[700,700]}),
])
def test_spinner_animation_does_not_make_other_model_actions_current(action):
    assert not proposal_is_current(action,loading_snapshot(),loading_snapshot(frame=4))


@pytest.mark.parametrize('box',[(20,200,30,210),(150,355,155,365),(270,355,275,365),
                               (104,350,104,381),(137,350,137,381)])
def test_loading_spinner_exception_keeps_map_label_cancel_and_boundary_pixels_exact(box):
    after=changed_pixels(loading_snapshot(frame=4),box,'red')
    assert not proposal_is_current(Action('Back',{}),loading_snapshot(),after)


@pytest.mark.parametrize('old,new',[
    ('正在加载...','无法加载'),('loading-window','another-window'),
    ('[105,350][137,382]','[106,350][138,382]'),
    ('clickable="true"','clickable="false"'),
    ('lottie_view','unknown_animation'),
])
def test_changed_loading_native_identity_cannot_reuse_an_earlier_action(old,new):
    before,after=loading_snapshot(),loading_snapshot(frame=4)
    after['xml']=after['xml'].replace(old,new)
    assert not proposal_is_current(Action('Back',{}),before,after)


@pytest.mark.parametrize('old,new',[
    ('正在加载...','其他弹窗'),('loading_text','other_text'),
    ('lottie_view','other_image'),('[105,350][137,382]','[105,330][245,410]'),
    ('visible-to-user="true"','visible-to-user="false"'),
])
def test_unknown_or_broad_loading_scenes_never_grant_a_spinner_exception(old,new):
    before,after=loading_snapshot(),loading_snapshot(frame=4)
    for shot in (before,after):
        shot['xml']=shot['xml'].replace(old,new)
    assert not proposal_is_current(Action('Back',{}),before,after)


@pytest.mark.parametrize('field,value',[
    ('epoch',2),('rotation',1),('width',401),('height',801),('current_app','com.example.demo'),
])
def test_loading_cancel_never_crosses_global_screen_context_changes(field,value):
    before,after=loading_snapshot(),loading_snapshot(frame=4);after[field]=value
    assert not proposal_is_current(Action('Tap',{'element':[700,460]}),before,after)


ARRIVAL_REQUEST={'origin':'体育西路地铁站','destination':'广州塔地铁站',
                 'city':'广州市','preference':'fewer_transfers'}


def arrival_snapshot(*,frame=0,clock=0):
    shot=snapshot(clock=clock)
    shot.update(screen_width=W,screen_height=H,current_package='com.autonavi.minimap')
    shot.pop('caret_context',None)
    root=ET.Element('hierarchy',{'window-id':'arrival-route-list'})
    def node(parent,cls,box,**attrs):
        return ET.SubElement(parent,'node',{'package':'com.autonavi.minimap','class':cls,
            'bounds':f'[{box[0]},{box[1]}][{box[2]},{box[3]}]',
            'enabled':'true','visible-to-user':'true','clickable':'true','resource-id':'',**attrs})
    node(root,'android.widget.EditText',[60,60,350,110],text='我的位置',
         **{'resource-id':'com.autonavi.minimap:id/route_edit_summary_start'})
    node(root,'android.widget.EditText',[60,110,350,160],text='广州塔地铁站',
         **{'resource-id':'com.autonavi.minimap:id/route_edit_summary_end'})
    node(root,'android.widget.TextView',[130,180,270,220],text='公共交通',selected='true',
         **{'resource-id':'com.autonavi.minimap:id/route_input_conbine_text'})
    card=node(root,'android.view.ViewGroup',[0,400,400,700],
              **{'content-desc':'30分钟3号线预计22:47进站'})
    body=node(card,'android.view.ViewGroup',[10,410,390,690])
    row=node(body,'android.view.ViewGroup',[250,640,380,676])
    icon_owner=node(row,'android.view.ViewGroup',[250,648,270,668])
    node(icon_owner,'android.widget.ImageView',[250,648,270,668],clickable='false',
         **{'content-desc':'图标'})
    caption_owner=node(row,'android.view.ViewGroup',[274,640,378,676])
    node(caption_owner,'android.view.View',[274,640,378,676],text='预计22:47进站',clickable='false')
    shot['xml']=ET.tostring(root,encoding='unicode')
    return changed_pixels(shot,(254+frame,652,256+frame,664),'seagreen')


def arrival_current(action,before,after,**kwargs):
    from autoglm.freshness import visual_decorations_only
    return visual_decorations_only(action,before,after,request=ARRIVAL_REQUEST,state={},**kwargs)


@pytest.mark.parametrize('point',[[500,100],[500,160]])
def test_tiny_native_live_arrival_icon_can_animate_while_entering_the_same_endpoint(point):
    action=Action('Tap',{'element':point})
    assert arrival_current(action,arrival_snapshot(),arrival_snapshot(frame=4))
    assert arrival_current(action,arrival_snapshot(),arrival_snapshot(frame=4,clock=1))


@pytest.mark.parametrize('action',[
    Action('Tap',{'element':[650,820]}),  # arrival icon itself
    Action('Tap',{'element':[500,600]}),  # route-card selection
    Action('Tap',{'element':[500,250]}), # transit tab
    Action('Type',{'text':'体育西路地铁站'}),Action('Finish',{'message':'完成'}),
    Action('Back',{}),Action('Swipe',{'start':[500,800],'end':[500,500]}),
])
def test_live_arrival_animation_does_not_exempt_any_other_action_or_target(action):
    assert not arrival_current(action,arrival_snapshot(),arrival_snapshot(frame=4))


@pytest.mark.parametrize('box',[(60,65,62,67),(280,645,282,650),(20,300,22,302),
                               (249,648,249,667),(270,648,270,667)])
def test_arrival_icon_exception_preserves_header_eta_map_and_boundary_pixels(box):
    after=changed_pixels(arrival_snapshot(frame=4),box,'red')
    assert not arrival_current(Action('Tap',{'element':[500,100]}),arrival_snapshot(),after)


@pytest.mark.parametrize('old,new',[
    ('我的位置','另一个起点'),('广州塔地铁站','另一个终点'),
    ('预计22:47进站','预计22:48进站'),('arrival-route-list','another-window'),
    ('[250,648][270,668]','[251,648][271,668]'),
])
def test_live_arrival_helper_itself_rejects_any_native_or_window_change(old,new):
    before,after=arrival_snapshot(),arrival_snapshot(frame=4)
    after['xml']=after['xml'].replace(old,new)
    # Direct helper invocation proves it enforces full native identity itself,
    # rather than depending only on the executor's preceding signature check.
    assert not arrival_current(Action('Tap',{'element':[500,100]}),before,after)


def test_even_invisible_eta_native_change_is_not_ignored_for_header_entry():
    before,after=arrival_snapshot(),arrival_snapshot(frame=4)
    for shot,text in [(before,'9分钟·4站'),(after,'8分钟·4站')]:
        root=ET.fromstring(shot['xml'])
        ET.SubElement(root,'node',{'text':text,'bounds':'[300,900][400,1000]',
                                 'visible-to-user':'false','package':'com.autonavi.minimap'})
        shot['xml']=ET.tostring(root,encoding='unicode')
    assert not arrival_current(Action('Tap',{'element':[500,100]}),before,after)


@pytest.mark.parametrize('old,new',[
    ('预计22:47进站','任意文字'),('预计22:47进站','预计29:99进站'),
    ('content-desc="图标"','content-desc="其他图标"'),
    ('android.widget.ImageView','android.widget.Button'),
    ('[250,648][270,668]','[200,620][270,690]'),
    ('selected="true"','selected="false"'),
    ('30分钟3号线预计22:47进站','广告预计22:47进站'),
])
def test_generic_or_unbound_arrival_like_controls_do_not_supply_an_ignored_region(old,new):
    before,after=arrival_snapshot(),arrival_snapshot(frame=4)
    for shot in (before,after):shot['xml']=shot['xml'].replace(old,new)
    assert not arrival_current(Action('Tap',{'element':[500,100]}),before,after)


@pytest.mark.parametrize('field,value',[
    ('epoch',2),('rotation',1),('width',401),('height',801),('current_app','com.example.demo'),
])
def test_live_arrival_exception_never_crosses_global_screen_context(field,value):
    before,after=arrival_snapshot(),arrival_snapshot(frame=4);after[field]=value
    assert not arrival_current(Action('Tap',{'element':[500,100]}),before,after)


def test_arrival_exception_needs_task_authority_and_two_freshly_groundable_fields():
    from autoglm.freshness import visual_decorations_only
    before,after=arrival_snapshot(),arrival_snapshot(frame=4)
    action=Action('Tap',{'element':[500,100]})
    assert not visual_decorations_only(action,before,after)
    assert not visual_decorations_only(action,before,after,request={})
    for shot in (before,after):
        shot['xml']=shot['xml'].replace('route_edit_summary_start','unknown_edit')
    assert not arrival_current(action,before,after)


def test_executor_handoff_preserves_task_authority_for_the_arrival_icon_exception():
    action=Action('Tap',{'element':[500,100]})
    before,after=arrival_snapshot(),arrival_snapshot(frame=4)
    assert not proposal_is_current(action,before,after)
    assert proposal_is_current(action,before,after,request=ARRIVAL_REQUEST,state={})
    after['xml']=after['xml'].replace('预计22:47进站','预计22:48进站')
    assert not proposal_is_current(action,before,after,request=ARRIVAL_REQUEST,state={})


def test_extra_overlay_child_cannot_borrow_the_native_arrival_icon_region():
    before,after=arrival_snapshot(),arrival_snapshot(frame=4)
    for shot in (before,after):
        root=ET.fromstring(shot['xml'])
        parents={child:parent for parent in root.iter() for child in parent}
        icon=next(n for n in root.iter('node') if n.get('content-desc')=='图标')
        ET.SubElement(parents[icon],'node',{'class':'android.view.View','package':'com.autonavi.minimap',
            'bounds':'[254,652][265,665]','visible-to-user':'true','enabled':'true'})
        shot['xml']=ET.tostring(root,encoding='unicode')
    assert not arrival_current(Action('Tap',{'element':[500,100]}),before,after)


def preference_arrival_snapshot(*,frame=0,clock=0,eta='预计22:47进站',preference='换乘少'):
    shot=arrival_snapshot(frame=frame,clock=clock)
    shot['xml']=shot['xml'].replace('预计22:47进站',eta)
    root=ET.fromstring(shot['xml'])
    owner=ET.SubElement(root,'node',{'package':'com.autonavi.minimap','class':'android.view.ViewGroup',
        'bounds':'[60,250][180,300]','text':'','content-desc':preference,'resource-id':'',
        'enabled':'true','visible-to-user':'true','clickable':'true'})
    ET.SubElement(owner,'node',{'package':'com.autonavi.minimap','class':'android.view.View',
        'bounds':'[80,260][160,290]','text':preference,'content-desc':'','resource-id':'',
        'enabled':'true','visible-to-user':'true','clickable':'false'})
    shot['xml']=ET.tostring(root,encoding='unicode')
    return shot


@pytest.mark.parametrize('eta',['预计22:47进站','即将进站'])
@pytest.mark.parametrize('clock',[0,1])
def test_same_task_grounded_preference_can_ignore_only_proven_arrival_icon(eta,clock):
    before=preference_arrival_snapshot(eta=eta)
    after=preference_arrival_snapshot(frame=4,clock=clock,eta=eta)
    action=Action('Tap',{'element':[300,340]})
    assert arrival_current(action,before,after)
    assert proposal_is_current(action,before,after,request=ARRIVAL_REQUEST,state={})


@pytest.mark.parametrize('eta',['即将到达','即将到站','即将进其他站','其他站','预计29:99进站','任意ETA'])
def test_preference_arrival_exception_never_accepts_arbitrary_eta_captions(eta):
    before=preference_arrival_snapshot(eta=eta)
    after=preference_arrival_snapshot(frame=4,eta=eta)
    assert not arrival_current(Action('Tap',{'element':[300,340]}),before,after)


@pytest.mark.parametrize('action',[
    Action('Tap',{'element':[650,820]}),Action('Tap',{'element':[500,600]}),
    Action('Tap',{'element':[500,250]}),Action('Type',{'text':'体育西路地铁站'}),
    Action('Swipe',{'start':[500,800],'end':[500,500]}),Action('Finish',{'message':'完成'}),Action('Back',{}),
])
def test_preference_extension_does_not_exempt_route_selection_or_other_actions(action):
    assert not arrival_current(action,preference_arrival_snapshot(),preference_arrival_snapshot(frame=4))


@pytest.mark.parametrize('mutation',['eta_text','target_text','target_bounds','window','invisible_eta','current_package'])
def test_preference_arrival_exception_requires_full_native_target_and_window_identity(mutation):
    before,after=preference_arrival_snapshot(),preference_arrival_snapshot(frame=4)
    if mutation=='eta_text':after['xml']=after['xml'].replace('预计22:47进站','预计22:48进站')
    elif mutation=='target_text':after['xml']=after['xml'].replace('换乘少','少换乘')
    elif mutation=='target_bounds':after['xml']=after['xml'].replace('[80,260][160,290]','[81,260][161,290]')
    elif mutation=='window':after['xml']=after['xml'].replace('arrival-route-list','different-window')
    elif mutation=='current_package':after['current_package']='other.app'
    else:
        for shot,text in [(before,'8分钟'),(after,'7分钟')]:
            root=ET.fromstring(shot['xml'])
            ET.SubElement(root,'node',{'package':'com.autonavi.minimap','class':'android.view.View',
                'text':text,'bounds':'[10,720][100,750]','visible-to-user':'false'})
            shot['xml']=ET.tostring(root,encoding='unicode')
    assert not arrival_current(Action('Tap',{'element':[300,340]}),before,after)


@pytest.mark.parametrize('box',[(100,265,110,280),(280,645,282,650),(20,300,22,302),(270,650,270,655)])
def test_preference_arrival_exception_never_ignores_control_eta_map_or_icon_boundary_pixels(box):
    before=preference_arrival_snapshot()
    after=changed_pixels(preference_arrival_snapshot(frame=4),box,'red')
    assert not arrival_current(Action('Tap',{'element':[300,340]}),before,after)


def test_other_preference_or_nonpublic_scene_cannot_use_arrival_exception():
    before=preference_arrival_snapshot(preference='步行少')
    after=preference_arrival_snapshot(frame=4,preference='步行少')
    action=Action('Tap',{'element':[300,340]})
    assert not arrival_current(action,before,after)
    before,after=preference_arrival_snapshot(),preference_arrival_snapshot(frame=4)
    for shot in (before,after):shot['xml']=shot['xml'].replace('selected="true"','selected="false"')
    assert not arrival_current(action,before,after)


CARD_QUERY='offline-card-query'
CARD_REQUEST={'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市','preference':'fewer_transfers'}


def canonical_card_snapshot(*,frame=0,clock=False,animate_second=False):
    root=ET.Element('hierarchy',{'window-id':'canonical-card-window','nonce':'capture-only'})
    def node(parent,cls,box,**attrs):
        return ET.SubElement(parent,'node',{'package':'com.autonavi.minimap','class':cls,
            'bounds':f'[{box[0]},{box[1]}][{box[2]},{box[3]}]',
            'enabled':'true','visible-to-user':'true','clickable':'true','resource-id':'',**attrs})
    for suffix,text,y in [('start','体育西路(地铁站)',213),('end','广州塔(地铁站)',312)]:
        node(root,'android.widget.EditText',[256,y,828,y+62],text=text,
             **{'resource-id':'com.autonavi.minimap:id/route_edit_summary_'+suffix,
                'content-desc':'起点输入栏' if suffix=='start' else '终点输入栏'})
    node(root,'android.widget.TextView',[586,441,770,503],text='公共交通',selected='true',clickable='false',
         **{'resource-id':'com.autonavi.minimap:id/route_input_conbine_text'})
    node(root,'android.view.View',[1000,596,1200,664],text='换乘少',clickable='false')
    def card(offset=0,minutes=6):
        def box(values):return [values[0],values[1]+offset,values[2],values[3]+offset]
        summary=f'{minutes}分钟3号线2站¥2'
        outer=node(root,'android.view.ViewGroup',box([0,742,1200,1298]),**{'content-desc':summary+'即将进站'})
        body=node(outer,'android.view.ViewGroup',box([26,742,1173,1290]))
        inner=node(body,'android.view.ViewGroup',box([79,795,1120,1072]),**{'content-desc':summary})
        node(inner,'android.view.View',box([79,795,1120,1072]),text=summary,clickable='false')
        row=node(body,'android.view.ViewGroup',box([887,1154,1120,1220]))
        owner=node(row,'android.view.ViewGroup',box([887,1165,930,1208]))
        node(owner,'android.widget.ImageView',box([887,1165,930,1208]),clickable='false',**{'content-desc':'图标'})
        caption=node(row,'android.view.ViewGroup',box([936,1154,1120,1220]))
        node(caption,'android.view.View',box([936,1154,1120,1220]),text='即将进站',clickable='false')
    card()
    if animate_second:card(658,7)
    image=Image.new('RGB',(1200,2640),'white');draw=ImageDraw.Draw(image)
    offset=658 if animate_second else 0
    draw.rectangle([890+frame,1170+offset,897+frame,1200+offset],fill='seagreen')
    if clock:draw.rectangle([901,96,941,119],fill='black')
    return {'epoch':1,'width':1200,'height':2640,'screen_width':1200,'screen_height':2640,
            'rotation':0,'current_app':'com.autonavi.minimap','current_package':'com.autonavi.minimap',
            'xml':ET.tostring(root,encoding='unicode'),'screenshot_base64':encoded(image),
            'status_bar_frame':[0,0,1200,154],
            'evidence':{'query_id':CARD_QUERY,'evidence_id':'current-card-frame'}}


def canonical_card_state():
    from device.amap import AmapDevice
    adapter=AmapDevice.__new__(AmapDevice)  # Pure native parser, no config/device.
    adapter.query_id=CARD_QUERY
    adapter.request=dict(CARD_REQUEST)
    adapter.observations=[{'query_id':CARD_QUERY,'evidence_id':ref,'page_kind':'place_detail'}
                          for ref in ['origin-proof','destination-proof']]
    places={role:{'name':name,'city':'广州市','source':'explicit',
                  'evidence_refs':{field:[role+'-proof'] for field in ['name','city']}}
            for role,name in [('origin','体育西路(地铁站)'),('destination','广州塔(地铁站)')]}
    # This is an offline conditional fixture, not evidence of a real filter tap.
    return {'adapter':adapter,'places':places,'choices':{},'filter':'换乘少'}


def card_current(action,before,after,state=None):
    return proposal_is_current(action,before,after,request=CARD_REQUEST,
                               state=canonical_card_state() if state is None else state)


@pytest.mark.parametrize('clock',[False,True])
def test_same_canonical_route_card_can_tolerate_only_its_small_native_arrival_icon(clock):
    from autoglm.safety import _canonical_route_cards,resolve_action
    state=canonical_card_state();before=canonical_card_snapshot();after=canonical_card_snapshot(frame=4,clock=clock)
    native={**before,'nodes':list(ET.fromstring(before['xml']).iter('node'))}
    assert len(state['adapter']._route_cards(native))==2
    assert len(_canonical_route_cards(native,state['adapter']))==1
    grounded=resolve_action(Action('Tap',{'element':[500,340]}),native,CARD_REQUEST,state)
    assert grounded.role=='route' and grounded.binding['route_summary']=='6分钟3号线2站¥2即将进站'
    assert card_current(Action('Tap',{'element':[500,340]}),before,after,state)


@pytest.mark.parametrize('box',[(80,800,81,801),(940,1160,941,1161),(20,1500,21,1501),
                               (886,1170,886,1180),(930,1170,930,1180)])
def test_card_exception_preserves_summary_eta_map_and_icon_boundary_pixels(box):
    before=canonical_card_snapshot();after=changed_pixels(canonical_card_snapshot(frame=4),box,'red')
    assert not card_current(Action('Tap',{'element':[500,340]}),before,after)


def test_icon_tap_or_another_cards_animation_cannot_borrow_card_freshness():
    assert not card_current(Action('Tap',{'element':[755,450]}),canonical_card_snapshot(),canonical_card_snapshot(frame=4))
    assert not card_current(Action('Tap',{'element':[500,340]}),canonical_card_snapshot(animate_second=True),
                            canonical_card_snapshot(frame=4,animate_second=True))


@pytest.mark.parametrize('mutation',['no_filter','other_filter','missing_poi','missing_city','wrong_city',
                                     'missing_name_ref','foreign_ref','foreign_source_query','missing_source',
                                     'different_adapter_query','selected_address_mismatch'])
def test_card_exception_requires_current_query_resolved_place_and_actual_filter_state(mutation):
    state=canonical_card_state()
    if mutation=='no_filter':state.pop('filter')
    elif mutation=='other_filter':state['filter']='高德推荐'
    elif mutation=='missing_poi':state['places'].pop('origin')
    elif mutation=='missing_city':state['places']['origin']['city']=None
    elif mutation=='wrong_city':state['places']['origin']['city']='深圳市'
    elif mutation=='missing_name_ref':state['places']['origin']['evidence_refs']['name']=[]
    elif mutation=='foreign_ref':state['places']['origin']['evidence_refs']['name']=['another-query-ref']
    elif mutation=='foreign_source_query':state['adapter'].observations[0]['query_id']='another-query'
    elif mutation=='missing_source':state['adapter'].observations=[]
    elif mutation=='different_adapter_query':state['adapter'].query_id='another-query'
    else:state['choices']['origin']={'address':'本人已确认的地址'}
    assert not card_current(Action('Tap',{'element':[500,340]}),canonical_card_snapshot(),canonical_card_snapshot(frame=4),state)


@pytest.mark.parametrize('mutation',['before_query','after_query','missing_query','native_text','eta_text',
                                     'window','native_header','foreign_overlay','different_path','public_mode'])
def test_card_exception_does_not_ignore_frame_query_or_any_native_identity(mutation):
    from autoglm.freshness import visual_decorations_only
    before,after=canonical_card_snapshot(),canonical_card_snapshot(frame=4)
    if mutation=='before_query':before['evidence']['query_id']='another-query'
    elif mutation=='after_query':after['evidence']['query_id']='another-query'
    elif mutation=='missing_query':after['evidence']={}
    elif mutation=='native_text':after['xml']=after['xml'].replace('6分钟','5分钟')
    elif mutation=='eta_text':after['xml']=after['xml'].replace('即将进站','预计22:47进站')
    elif mutation=='window':after['xml']=after['xml'].replace('canonical-card-window','another-window')
    elif mutation=='native_header':after['xml']=after['xml'].replace('体育西路(地铁站)','我的位置')
    elif mutation=='public_mode':after['xml']=after['xml'].replace('selected="true"','selected="false"')
    else:
        root=ET.fromstring(after['xml'])
        if mutation=='foreign_overlay':ET.SubElement(root,'node',{'package':'other.app','class':'android.view.View',
              'bounds':'[590,880][620,910]','visible-to-user':'true','clickable':'true'})
        else:root.insert(0,ET.Element('node',{'package':'com.autonavi.minimap','bounds':'[10,700][11,701]'}))
        after['xml']=ET.tostring(root,encoding='unicode')
    # Also test the helper directly: it independently rejects native changes.
    assert not visual_decorations_only(Action('Tap',{'element':[500,340]}),before,after,
                                       request=CARD_REQUEST,state=canonical_card_state())


@pytest.mark.parametrize('field,value',[('epoch',2),('rotation',1),('width',1201),('height',2641),
                                        ('current_app','other.app'),('current_package','other.app')])
def test_card_exception_never_survives_global_context_change(field,value):
    before,after=canonical_card_snapshot(),canonical_card_snapshot(frame=4);after[field]=value
    assert not card_current(Action('Tap',{'element':[500,340]}),before,after)


@pytest.mark.parametrize('action',[Action('Type',{'text':'体育西路地铁站'}),Action('Back',{}),
    Action('Finish',{'message':'完成'}),Action('Swipe',{'start':[500,600],'end':[500,300]})])
def test_route_card_proof_never_expands_to_other_actions(action):
    assert not card_current(action,canonical_card_snapshot(),canonical_card_snapshot(frame=4))


def test_persistent_foreign_overlay_and_distinct_overlapping_cards_stay_rejected():
    for duplicate in (False,True):
        before,after=canonical_card_snapshot(),canonical_card_snapshot(frame=4)
        for shot in (before,after):
            root=ET.fromstring(shot['xml'])
            if duplicate:
                outer=next(n for n in root if n.get('content-desc','').startswith('6分钟'))
                root.append(copy.deepcopy(outer))
            else:
                ET.SubElement(root,'node',{'package':'other.app','class':'android.view.View',
                    'bounds':'[590,880][620,910]','visible-to-user':'true','enabled':'true','clickable':'true'})
            shot['xml']=ET.tostring(root,encoding='unicode')
        assert not card_current(Action('Tap',{'element':[500,340]}),before,after)


def test_resolved_poi_identity_cannot_be_replaced_by_request_text_only():
    state=canonical_card_state();state['places']['origin']['name']='另一个地铁站'
    assert not card_current(Action('Tap',{'element':[500,340]}),canonical_card_snapshot(),canonical_card_snapshot(frame=4),state)


@pytest.mark.parametrize('age,fresh',[(0,True),(121,False)])
def test_current_location_card_needs_independent_fresh_health_and_address_proof(age,fresh):
    import time
    request={**CARD_REQUEST,'origin':'','use_current_location':True}
    state=canonical_card_state()
    state.update(location_verified=True,location_health={'verified':True,'age_seconds':age},
                 location_checked_at=time.monotonic())
    state['places']['origin'].update(name='已核对的实际位置',source='current_location',address='广州市天河区测试地址')
    state['places']['origin']['evidence_refs']['address']=['origin-proof']
    state['adapter'].request=request
    before,after=canonical_card_snapshot(),canonical_card_snapshot(frame=4)
    for shot in (before,after):shot['xml']=shot['xml'].replace('体育西路(地铁站)','我的位置')
    assert proposal_is_current(Action('Tap',{'element':[500,340]}),before,after,request=request,state=state) is fresh


@pytest.mark.parametrize('page_kind',['user_request','route_list','screen','place_list',None])
def test_same_query_non_poi_records_cannot_supply_card_place_evidence(page_kind):
    state=canonical_card_state()
    for record in state['adapter'].observations:record['page_kind']=page_kind
    assert not card_current(Action('Tap',{'element':[500,340]}),canonical_card_snapshot(),canonical_card_snapshot(frame=4),state)


def horizontal_preference_arrival_snapshot(*,frame=0,clock=False):
    from test_autoglm_preference_scroll import preference_bar_frame
    native,_,_=preference_bar_frame()
    shot=canonical_card_snapshot(frame=frame,clock=clock)
    source=ET.fromstring(shot['xml'])
    card=next(n for n in source if n.get('content-desc','').startswith('6分钟'))
    root=ET.fromstring(native['xml']);root.set('window-id','horizontal-preference-window')
    root.append(card)
    shot['xml']=ET.tostring(root,encoding='unicode')
    return shot


@pytest.mark.parametrize('reverse,clock',[(False,False),(False,True),(True,False)])
def test_only_native_horizontal_preference_swipe_can_tolerate_the_live_arrival_icon(reverse,clock):
    from autoglm.safety import resolve_action
    start,end=[915,240],[415,240]
    if reverse:start,end=end,start
    action=Action('Swipe',{'start':start,'end':end})
    before,after=horizontal_preference_arrival_snapshot(),horizontal_preference_arrival_snapshot(frame=4,clock=clock)
    native={**before,'nodes':list(ET.fromstring(before['xml']).iter('node'))}
    grounded=resolve_action(action,native,CARD_REQUEST,{})
    assert grounded.role=='preference_scroll' and grounded.binding['preference_bar'] is True
    assert (grounded.start,grounded.end)==(tuple(int(v*d/1000) for v,d in zip(start,[1200,2640])),
                                          tuple(int(v*d/1000) for v,d in zip(end,[1200,2640])))
    assert proposal_is_current(action,before,after,request=CARD_REQUEST,state={})


@pytest.mark.parametrize('start,end',[
    ([750,235],[750,249]),  # Ordinary vertical scroll, even inside this bar.
    ([915,500],[415,500]),  # Map / route-card horizontal gesture.
    ([990,240],[415,240]),  # Outside the native bar's safe interior.
    ([915,240],[0,240]),    # Ends outside the proven gesture row.
    ([915,235],[415,249]),  # Excess diagonal motion.
])
def test_no_other_swipe_borrows_the_preference_arrival_exception(start,end):
    action=Action('Swipe',{'start':start,'end':end})
    assert not proposal_is_current(action,horizontal_preference_arrival_snapshot(),
        horizontal_preference_arrival_snapshot(frame=4),request=CARD_REQUEST,state={})


@pytest.mark.parametrize('box',[(700,625,701,626),(940,1160,941,1161),(20,1600,21,1601),
                               (886,1170,886,1180),(930,1170,930,1180)])
def test_preference_swipe_keeps_every_bar_eta_map_and_icon_boundary_pixel_exact(box):
    before=horizontal_preference_arrival_snapshot()
    after=changed_pixels(horizontal_preference_arrival_snapshot(frame=4),box,'red')
    assert not proposal_is_current(Action('Swipe',{'start':[915,240],'end':[415,240]}),before,after,
                                   request=CARD_REQUEST,state={})


@pytest.mark.parametrize('mutation',['bar_bounds','bar_class','chip_text','origin','window','unknown_overlay'])
def test_horizontal_preference_exception_requires_exact_native_bar_window_and_fields(mutation):
    from autoglm.freshness import visual_decorations_only
    before,after=horizontal_preference_arrival_snapshot(),horizontal_preference_arrival_snapshot(frame=4)
    if mutation=='bar_bounds':after['xml']=after['xml'].replace('[366,544][1200,716]','[367,544][1200,716]')
    elif mutation=='bar_class':after['xml']=after['xml'].replace('SpringHorizontalScrollView','HorizontalScrollView')
    elif mutation=='chip_text':after['xml']=after['xml'].replace('换乘少','其他偏好')
    elif mutation=='origin':after['xml']=after['xml'].replace('体育西路(地铁站)','我的位置')
    elif mutation=='window':after['xml']=after['xml'].replace('horizontal-preference-window','another-window')
    else:
        root=ET.fromstring(after['xml'])
        ET.SubElement(root,'node',{'package':'other.app','class':'android.view.View','bounds':'[700,600][800,660]',
                                  'visible-to-user':'true','enabled':'true','clickable':'false'})
        after['xml']=ET.tostring(root,encoding='unicode')
    assert not visual_decorations_only(Action('Swipe',{'start':[915,240],'end':[415,240]}),before,after,
                                       request=CARD_REQUEST,state={})


def test_even_two_valid_observations_cannot_ignore_a_persistent_cover_of_the_gesture():
    before,after=horizontal_preference_arrival_snapshot(),horizontal_preference_arrival_snapshot(frame=4)
    for shot in (before,after):
        root=ET.fromstring(shot['xml'])
        ET.SubElement(root,'node',{'package':'other.app','class':'android.view.View','bounds':'[700,600][800,660]',
                                  'visible-to-user':'true','enabled':'true','clickable':'false'})
        shot['xml']=ET.tostring(root,encoding='unicode')
    assert not proposal_is_current(Action('Swipe',{'start':[915,240],'end':[415,240]}),before,after,
                                   request=CARD_REQUEST,state={})


def test_preference_drag_down_in_bar_can_end_left_in_the_same_verified_row():
    from autoglm.safety import resolve_action
    action=Action('Swipe',{'start':[780,236],'end':[195,236]})
    before,after=horizontal_preference_arrival_snapshot(),horizontal_preference_arrival_snapshot(frame=4,clock=True)
    native={**before,'nodes':list(ET.fromstring(before['xml']).iter('node'))}
    grounded=resolve_action(action,native,CARD_REQUEST,{})
    assert grounded.role=='preference_scroll'
    assert grounded.binding['bounds']==[366,544,1200,716]
    assert grounded.binding['gesture_bounds']==[0,544,1200,716]
    assert grounded.start==(936,623) and grounded.end==(234,623)
    assert proposal_is_current(action,before,after,request=CARD_REQUEST,state={})


@pytest.mark.parametrize('start,end',[
    ([195,236],[780,236]),  # Down outside the child bar is never authorized.
    ([780,236],[195,300]),  # Up outside the known row.
    ([780,236],[0,236]),    # Up past the row's safe left edge.
    ([780,236],[999,236]),  # Up at the right edge; not a leftward exit.
])
def test_preference_row_end_exception_never_changes_where_the_touch_begins(start,end):
    action=Action('Swipe',{'start':start,'end':end})
    assert not proposal_is_current(action,horizontal_preference_arrival_snapshot(),
        horizontal_preference_arrival_snapshot(frame=4),request=CARD_REQUEST,state={})


def test_unknown_cover_in_the_left_drag_path_is_not_ignored_as_an_arrival_animation():
    before,after=horizontal_preference_arrival_snapshot(),horizontal_preference_arrival_snapshot(frame=4)
    for shot in (before,after):
        root=ET.fromstring(shot['xml'])
        ET.SubElement(root,'node',{'package':'other.app','class':'android.view.View','bounds':'[300,600][350,660]',
                                  'visible-to-user':'true','enabled':'true','clickable':'false'})
        shot['xml']=ET.tostring(root,encoding='unicode')
    assert not proposal_is_current(Action('Swipe',{'start':[780,236],'end':[195,236]}),before,after,
                                   request=CARD_REQUEST,state={})


def assist_transit_state():
    from types import SimpleNamespace
    return {'adapter':SimpleNamespace(query_id='current-query',_route_cards=lambda shot:[],_place_candidates=lambda *a,**k:[]),
            'assist_transit_precondition':True}


@pytest.mark.parametrize('mutation',['none','outside','target_pixel','native_target','window','other_query',
                                    'unbound','missing_insets','wrong_insets','epoch','package'])
def test_rule_assist_transit_only_tolerates_proven_arrival_icon(mutation):
    before, after=arrival_snapshot(),arrival_snapshot(frame=4,clock=1)
    for frame in (before,after):frame['evidence']={'query_id':'current-query'}
    state=assist_transit_state(); action=Action('Tap',{'element':[500,250]})
    if mutation=='outside':after=changed_pixels(after,(20,300,22,302),'red')
    elif mutation=='target_pixel':after=changed_pixels(after,(200,200,201,201),'red')
    elif mutation=='native_target':after['xml']=after['xml'].replace('公共交通','公交地铁')
    elif mutation=='window':after['xml']=after['xml'].replace('arrival-route-list','other-window')
    elif mutation=='other_query':after['evidence']['query_id']='different-query'
    elif mutation=='unbound':state['assist_transit_precondition']=False
    elif mutation=='missing_insets':before.pop('status_bar_frame')
    elif mutation=='wrong_insets':after['status_bar_frame']=[0,0,W,27]
    elif mutation=='epoch':after['epoch']+=1
    elif mutation=='package':after['current_app']='com.other'
    assert proposal_is_current(action,before,after,request=ARRIVAL_REQUEST,state=state) is (mutation=='none')


def test_rule_assist_arrival_exception_never_applies_to_other_target_or_general_agent():
    before,after=arrival_snapshot(),arrival_snapshot(frame=4)
    for frame in (before,after):frame['evidence']={'query_id':'current-query'}
    state=assist_transit_state()
    assert not proposal_is_current(Action('Tap',{'element':[650,820]}),before,after,request=ARRIVAL_REQUEST,state=state)
    state.pop('assist_transit_precondition')
    assert not proposal_is_current(Action('Tap',{'element':[500,250]}),before,after,request=ARRIVAL_REQUEST,state=state)
