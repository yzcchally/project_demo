import pytest

from agent.local_intake import understand_local_instruction


@pytest.mark.parametrize(("instruction", "origin", "use_location"), [
    ("在广州，从广州塔地铁站去客村地铁站，坐地铁或公交，尽量少换乘。", "广州塔地铁站", False),
    ("在广州，从当前位置去客村地铁站，坐地铁或公交，尽量少换乘。", "当前位置", True),
    ("在广州，我想去客村地铁站，坐地铁或公交，尽量少换乘。", "", False),
])
def test_transit_form_preserves_start_location_choice(instruction, origin, use_location):
    result = understand_local_instruction(instruction)

    assert result.task_type == "plan_transit"
    assert result.transit.destination == "客村地铁站"
    assert result.transit.origin == origin
    assert result.transit.use_current_location == use_location


@pytest.mark.parametrize("instruction", [
    "扫码", "扫一扫", "  麻烦帮我扫一下这个二维码！ ", "扫个码", "打开扫码",
    "能不能帮我扫码一下？", "请扫描二维码", "帮我扫这个二维码，打开网页。",
])
def test_direct_scan_opens_the_camera_without_requesting_mail(instruction):
    result = understand_local_instruction(instruction)
    assert result.task_type == "open_url"
    assert result.needs_qr
    assert not result.recipient_alias


@pytest.mark.parametrize("instruction", [
    "扫码发给女儿", "不要扫码", "扫码付款", "这是什么二维码", "扫一下二维码，顺便发邮件",
    "帮我识别镜头前这个码",
])
def test_only_unambiguous_scan_commands_are_direct(instruction):
    assert understand_local_instruction(instruction) is None


def test_examples_are_deterministic_and_other_instructions_still_use_model():
    assert understand_local_instruction("帮我扫这个二维码，打开网页。").task_type == "open_url"
    assert understand_local_instruction("帮我扫码，把网页发邮件给女儿。").recipient_alias == "女儿"
    assert understand_local_instruction("在广州，从广州塔地铁站到客村地铁站怎么走？").transit.city == "广州"
    assert understand_local_instruction("帮我看看坐公交怎么去医院") is None
    assert understand_local_instruction("帮我扫这个二维码，打开网页。然后替我购买商品") is None
    assert understand_local_instruction("从起点去往去来广场，坐地铁或公交，尽量少换乘。") is None


@pytest.mark.parametrize("instruction", ["去大学城南", "我想去大学城南。", "怎么去大学城南？", "大学城南怎么走", "到大学城南怎么走？", "坐公交去大学城南"])
def test_short_destination_preserves_only_the_named_place(instruction):
    result = understand_local_instruction(instruction)
    assert result.task_type == "plan_transit"
    assert result.transit.destination == "大学城南"
    assert result.transit.origin == result.transit.city == ""
    assert not result.transit.use_current_location


@pytest.mark.parametrize("instruction", ["去", "去查天气", "去大学城南然后给女儿发邮件", "去大学城南并发送到我的钉钉文件助手", "从板桥到大学城南怎么走", "去大学城南，顺便打开网页", "我想去体育西路坐公交"])
def test_short_destination_does_not_capture_compound_or_non_route_commands(instruction):
    assert understand_local_instruction(instruction) is None
