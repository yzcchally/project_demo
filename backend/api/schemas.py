from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


DemoCaseId = Literal["T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08", "T09", "T10", "T11", "T12"]
DemoFault = Literal["composer-background"]
DEMO_CASE_IDS = tuple(f"T{number:02}" for number in range(1, 13))


def validate_demo_request(case_id, fault):
    if case_id is not None and case_id not in DEMO_CASE_IDS:
        raise ValueError("演示案例编号无效")
    if fault is not None and fault != "composer-background":
        raise ValueError("演示故障类型无效")
    if (case_id == "T12") != (fault == "composer-background"):
        raise ValueError("只有恢复演示 T12 可以明确启用一次写信页中断，且必须指定中断类型")


class TaskIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: Literal["scan_and_share", "share_url", "share_current_page", "open_url", "unsupported"]
    recipient_alias: str = Field(default="", max_length=80)
    recipient_address: str = Field(default="", max_length=254)
    subject: str = Field(default="网页分享", max_length=150)
    body: str = Field(default="", max_length=10000)
    url: str = Field(default="", max_length=2048)


class GoalRequest(BaseModel):
    instruction: str = Field(min_length=2, max_length=4000)
    request_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    assistance_mode: Literal["silverguide", "basic"] = "silverguide"
    scenario_id: Literal["N01", "N02", "N03", "N04", "R01", "R02", "R03", "R04", "S01", "S02", "S03", "S04"] | None = None
    third_party_assistance: bool | None = None
    demo_case_id: DemoCaseId | None = None
    demo_fault: DemoFault | None = None

    @model_validator(mode="after")
    def check_demo_request(self):
        validate_demo_request(self.demo_case_id, self.demo_fault)
        return self


class HumanReply(BaseModel):
    recipient: str = Field(default="", max_length=254)
    alias: str = Field(default="", max_length=80)
    save_contact: bool = False
    subject: str | None = Field(default=None, max_length=150)
    body: str | None = Field(default=None, max_length=10000)
    url: str | None = Field(default=None, max_length=2048)


class QRReply(BaseModel):
    url: str = Field(max_length=2048)
    scan_nonce: str = Field(min_length=16, max_length=100)


class Contact(BaseModel):
    alias: str = Field(min_length=1, max_length=80)
    address: str = Field(min_length=3, max_length=254)


class ActionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["open_app", "tap_element", "enter_text", "press_back", "swipe", "wait"]
    target_id: str = Field(default="", max_length=80)
    role: Literal["", "to", "subject", "body", "url"] = ""
    text: str = Field(default="", max_length=11000)
    package: Literal["", "com.huawei.email", "com.huawei.browser", "com.android.chrome", "com.example.demo"] = ""


class ScreenDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: Literal["composer", "browser", "mail_list", "unknown"]
    actions: list[ActionSpec] = Field(default_factory=list, max_length=6)
    explanation: str = Field(default="", max_length=200)
