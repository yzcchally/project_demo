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


def test_examples_are_deterministic_and_other_instructions_still_use_model():
    assert understand_local_instruction("帮我扫这个二维码，打开网页。").task_type == "open_url"
    assert understand_local_instruction("帮我扫码，把网页发邮件给女儿。").recipient_alias == "女儿"
    assert understand_local_instruction("在广州，从广州塔地铁站到客村地铁站怎么走？").transit.city == "广州"
    assert understand_local_instruction("帮我看看坐公交怎么去医院") is None
    assert understand_local_instruction("帮我扫这个二维码，打开网页。然后替我购买商品") is None
    assert understand_local_instruction("从起点去往去来广场，坐地铁或公交，尽量少换乘。") is None
