from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from device.android_app import android_package


DemoCaseId = Literal["T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08", "T09", "T10", "T11", "T12", "T13", "T14", "T15", "T16", "T17", "T18", "T19", "T20"]
DemoFault = Literal["composer-background"]
DEMO_CASE_IDS = tuple(f"T{number:02}" for number in range(1, 21))


def validate_demo_request(case_id, fault):
    if case_id is not None and case_id not in DEMO_CASE_IDS:
        raise ValueError("演示案例编号无效")
    if fault is not None and fault != "composer-background":
        raise ValueError("演示故障类型无效")
    if (case_id == "T12") != (fault == "composer-background"):
        raise ValueError("只有恢复演示 T12 可以明确启用一次写信页中断，且必须指定中断类型")


TransitPreference = Literal["recommended", "fewer_transfers", "less_walking"]


class TransitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    origin: str = Field(default="", max_length=160)
    destination: str = Field(default="", max_length=160)
    city: str = Field(default="", max_length=80)
    preference: TransitPreference = "fewer_transfers"
    use_current_location: bool = False

    @model_validator(mode="after")
    def explicit_origin_wins(self):
        if self.origin and self.origin not in {"当前位置", "我的位置", "这里", "现在的位置"}:
            self.use_current_location = False
        return self


class TransitPlace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=200)
    address: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=80)
    source: Literal["explicit", "current_location", "selected_ui"] = "selected_ui"
    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)


class TransitLocationStatus(BaseModel):
    """Read-only system health attached to the current GUI location evidence.

    A UI observation time is not a position fix time. Unknown fields stay null
    and do not certify location health; the independent verifier checks them.
    """
    model_config = ConfigDict(extra="forbid")
    verified: bool = False
    reason: str = Field(default="", max_length=200)
    enabled: bool | None = None
    permission_granted: bool | None = None
    app_access_recent: bool | None = None
    provider: str | None = Field(default=None, max_length=40)
    age_seconds: float | None = Field(default=None, ge=0)
    accuracy_m: float | None = Field(default=None, gt=0)
    is_mock: bool | None = None
    checked_at: str | None = Field(default=None, max_length=80)
    fix_time: str | None = Field(default=None, max_length=80)
    user_id: int | None = Field(default=None, ge=0)
    appops_mode: str | None = Field(default=None, max_length=40)
    app_access_age_seconds: float | None = Field(default=None, ge=0)
    clock_basis: str | None = Field(default=None, max_length=120)


class TransitEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1, max_length=120)
    query_id: str = Field(min_length=1, max_length=80)
    route_id: str | None = Field(default=None, max_length=120)
    observation_id: str = Field(default="", max_length=120)
    text: str = Field(default="", max_length=40000)
    xml_path: str | None = Field(default=None, max_length=400)
    screenshot_path: str | None = Field(default=None, max_length=400)
    location_status: TransitLocationStatus | None = None
    page_kind: Literal["location", "search", "place_list", "place_detail", "route_input", "route_list", "route_detail", "user_request", "screen", "unknown"] = "unknown"


class TransitSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1, max_length=120)
    mode: Literal["walk", "bus", "subway"]
    line: str | None = Field(default=None, max_length=160)
    direction: str | None = Field(default=None, max_length=160)
    board: str | None = Field(default=None, max_length=200)
    alight: str | None = Field(default=None, max_length=200)
    stops: int | None = Field(default=None, ge=0, le=300)
    instructions: str | None = Field(default=None, max_length=1000)
    entrance: str | None = Field(default=None, max_length=160)
    exit: str | None = Field(default=None, max_length=160)
    duration_minutes: float | None = Field(default=None, ge=0, le=1440)
    walking_meters: float | None = Field(default=None, ge=0, le=200000)
    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)


class TransitResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=80)
    query_id: str = Field(min_length=1, max_length=80)
    route_id: str = Field(min_length=1, max_length=120)
    source_app: Literal["高德地图"] = "高德地图"
    queried_at: str = Field(default="", max_length=80)
    collected_at: str = Field(default="", max_length=80)
    origin: TransitPlace = Field(default_factory=TransitPlace)
    destination: TransitPlace = Field(default_factory=TransitPlace)
    preference: TransitPreference = "fewer_transfers"
    applied_filter: str | None = Field(default=None, max_length=160)
    selection_basis: str = Field(default="仅在本次实际读取的候选中选择，未声称全局最优", max_length=1000)
    duration_minutes: float | None = Field(default=None, ge=0, le=1440)
    walking_meters: float | None = Field(default=None, ge=0, le=200000)
    transfers: int | None = Field(default=None, ge=0, le=30)
    fare_yuan: float | None = Field(default=None, ge=0, le=10000)
    segments: list[TransitSegment] = Field(default_factory=list, max_length=50)
    evidence: list[TransitEvidence] = Field(default_factory=list, max_length=80)
    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)
    classification: Literal["COMPLETE", "PARTIAL", "NO_ROUTE", "FAILED"] = "PARTIAL"
    missing_fields: list[str] = Field(default_factory=list, max_length=100)
    route_verified: bool = False
    failure_reason: str | None = Field(default=None, max_length=1000)
    derived: dict[str, str] = Field(default_factory=dict)


class TransitClarification(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query_id: str = Field(min_length=8, max_length=80)
    origin: str | None = Field(default=None, max_length=160)
    destination: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    use_current_location: bool | None = None
    preference: TransitPreference | None = None


class TransitPlaceChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(min_length=8, max_length=80)
    choice_nonce: str = Field(min_length=16, max_length=120)
    candidate_id: str = Field(min_length=1, max_length=120)


class TransitRequery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(min_length=8, max_length=80)
    preference: TransitPreference | None = None


class TransitQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query_id: str = Field(min_length=8, max_length=80)
    question: str = Field(min_length=1, max_length=500)


class TransitQueryRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(min_length=8, max_length=80)


class TransitAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["tap_element", "enter_text", "scroll", "wait"]
    target_id: str = Field(default="", max_length=120)
    role: Literal["", "origin", "destination", "search", "place", "transit", "route", "details", "back", "preference", "scroll"] = ""
    text: str = Field(default="", max_length=500)
    direction: Literal["up", "down"] = "up"


class TransitScreenDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str = Field(min_length=1, max_length=120)
    page: Literal["search", "place_list", "place_detail", "route_input", "route_list", "route_detail", "permission", "unknown"]
    actions: list[TransitAction] = Field(default_factory=list, max_length=4)
    explanation: str = Field(default="", max_length=300)


class TaskIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: Literal["scan_and_share", "share_url", "share_current_page", "open_url", "plan_transit", "contact_family", "unsupported"]
    transit: TransitRequest | None = None
    needs_qr: bool = Field(default=False, description="打开网页前是否需要从相机读取二维码。")
    family_recipient_alias: str = Field(default="", max_length=80, description="通话对象在用户原话中的称呼。")
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
    scan_source: Literal["in_app_camera", "external_share"] = "in_app_camera"


class Contact(BaseModel):
    alias: str = Field(min_length=1, max_length=80)
    address: str = Field(min_length=3, max_length=254)


def _action_packages() -> tuple[str, ...]:
    return ("", "com.huawei.email", "com.huawei.browser", "com.android.chrome", android_package())


class ActionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["open_app", "tap_element", "enter_text", "press_back", "swipe", "wait"]
    target_id: str = Field(default="", max_length=80)
    role: Literal["", "to", "subject", "body", "url"] = ""
    text: str = Field(default="", max_length=11000)
    package: str = Field(
        default="", strict=True, json_schema_extra=lambda schema: schema.update(enum=list(_action_packages())),
    )

    @field_validator("package")
    @classmethod
    def validate_package(cls, package: str) -> str:
        if package not in _action_packages():
            raise ValueError("不允许的应用包名")
        return package


class ScreenDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: Literal["composer", "browser", "mail_list", "unknown"]
    actions: list[ActionSpec] = Field(default_factory=list, max_length=6)
    explanation: str = Field(default="", max_length=200)
