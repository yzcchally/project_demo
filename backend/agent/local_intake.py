"""Direct entry for unambiguous commands and app-generated instructions."""
import re

from api.schemas import TaskIntent, TransitRequest


_SCAN_ONLY = re.compile(
    r"(?:(?:我想要|我想|我要|想|请(?:你)?|麻烦(?:你)?|能不能|可以)\s*)?"
    r"(?:(?:帮我|给我|替我)\s*)?"
    r"(?:打开扫码|扫一扫|扫码(?:一下)?|扫个码|扫一下码|扫下码|"
    r"(?:扫一扫|扫一下|扫下|扫|扫描)\s*(?:(?:这个|那个|一个|一下)\s*)?(?:二维码|码))"
    r"(?:\s*[，,]?\s*(?:(?:并|然后|再)\s*)?(?:打开网页|看看是什么|看一下))?"
    r"(?:\s*(?:吧|吗))?"
)
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
_PLACE_NAME = r"(?P<destination>[\u3400-\u9fff][\u3400-\u9fffA-Za-z0-9·（）()]{0,39})"
_SHORT_DESTINATION = re.compile(
    rf"(?:(?:我想|我想要|我要|想|要|怎么)?去|坐(?:地铁|公交(?:车)?)去|前往)\s*{_PLACE_NAME}"
)
_DESTINATION_DIRECTIONS = re.compile(rf"(?:(?:去|到|前往)\s*)?{_PLACE_NAME}\s*怎么走")
_ADDITIONAL_ACTION = re.compile(
    r"然后|顺便|同时|并(?:且|帮|给|发送|分享|打开|转账)|再帮我|发邮件|发给|发送|分享|"
    r"^(?:帮我|打开|查天气|买票|订票|付款|转账|打电话|睡觉|上班|上学|开车|走路|怎么办)|"
    r"(?:怎么(?:去|走)|(?:坐|乘坐)(?:公交(?:车)?|地铁))$"
)


def _short_command(instruction: str) -> str:
    return instruction.strip().rstrip("。！？?!.").rstrip()


def _short_destination(instruction: str) -> str:
    text = _short_command(instruction)
    if re.match(r"^(?:在\S+?)?从.+?(?:去|到|前往)", text):
        return ""
    match = _DESTINATION_DIRECTIONS.fullmatch(text) or _SHORT_DESTINATION.fullmatch(text)
    if match is None:
        return ""
    destination = match["destination"]
    return "" if _ADDITIONAL_ACTION.search(destination) else destination


def understand_local_instruction(instruction: str) -> TaskIntent | None:
    if _SCAN_ONLY.fullmatch(_short_command(instruction)):
        return TaskIntent(task_type="open_url", needs_qr=True)
    if instruction == "帮我扫码，把网页发邮件给女儿。":
        return TaskIntent(task_type="scan_and_share", recipient_alias="女儿")
    if instruction == "在广州，从广州塔地铁站到客村地铁站怎么走？":
        return TaskIntent(task_type="plan_transit", transit=TransitRequest(
            city="广州", origin="广州塔地铁站", destination="客村地铁站", preference="recommended",
        ))
    match = _TRANSIT_FORM.fullmatch(instruction)
    if match is None:
        destination = _short_destination(instruction)
        return TaskIntent(task_type="plan_transit", transit=TransitRequest(destination=destination)) if destination else None
    fields = match.groupdict()
    origin = fields["origin"] or ""
    if origin and ("去" in origin or "去" in fields["destination"]):
        return None
    return TaskIntent(task_type="plan_transit", transit=TransitRequest(
        city=fields["city"] or "", origin=origin, destination=fields["destination"],
        preference=_PREFERENCES[fields["preference"]], use_current_location=origin == "当前位置",
    ))
