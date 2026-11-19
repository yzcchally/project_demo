"""Intake for the app's exact examples and the explicit transit form."""
import re

from api.schemas import TaskIntent, TransitRequest


_TRANSIT_FORM = re.compile(
    r"(?:在(?P<city>[^，\n]{1,80})，)?"
    r"(?:从(?P<origin>[^，\n]{1,160})去|(?P<unspecified>我想去))"
    r"(?P<destination>[^，\n]{1,160})，坐地铁或公交，"
    r"(?P<preference>尽量少换乘|尽量少步行|选择高德推荐方案)。"
)
_PREFERENCES = {
    "尽量少换乘": "fewer_transfers",
    "尽量少步行": "less_walking",
    "选择高德推荐方案": "recommended",
}


def understand_local_instruction(instruction: str) -> TaskIntent | None:
    if instruction == "帮我扫这个二维码，打开网页。":
        return TaskIntent(task_type="open_url")
    if instruction == "帮我扫码，把网页发邮件给女儿。":
        return TaskIntent(task_type="scan_and_share", recipient_alias="女儿")
    if instruction == "在广州，从广州塔地铁站到客村地铁站怎么走？":
        return TaskIntent(task_type="plan_transit", transit=TransitRequest(
            city="广州", origin="广州塔地铁站", destination="客村地铁站", preference="recommended",
        ))
    match = _TRANSIT_FORM.fullmatch(instruction)
    if match is None:
        return None
    fields = match.groupdict()
    origin = fields["origin"] or ""
    if origin and ("去" in origin or "去" in fields["destination"]):
        return None
    return TaskIntent(task_type="plan_transit", transit=TransitRequest(
        city=fields["city"] or "", origin=origin, destination=fields["destination"],
        preference=_PREFERENCES[fields["preference"]], use_current_location=origin == "当前位置",
    ))
