"""Every phone operation the backend is allowed to send."""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tap(_Command):
    type: Literal["tap"]
    x: int = Field(ge=0, le=10000)
    y: int = Field(ge=0, le=10000)


class LongPress(_Command):
    type: Literal["long_press"]
    x: int = Field(ge=0, le=10000)
    y: int = Field(ge=0, le=10000)


class Swipe(_Command):
    type: Literal["swipe"]
    x1: int = Field(ge=0, le=10000)
    y1: int = Field(ge=0, le=10000)
    x2: int = Field(ge=0, le=10000)
    y2: int = Field(ge=0, le=10000)
    duration_ms: int = Field(ge=50, le=2000)


class Zoom(_Command):
    type: Literal["zoom"]
    x: int = Field(ge=0, le=10000)
    y: int = Field(ge=0, le=10000)
    direction: Literal["in", "out"]
    distance: int = Field(ge=50, le=1500)


class InputText(_Command):
    type: Literal["input_text"]
    x: int = Field(ge=0, le=10000)
    y: int = Field(ge=0, le=10000)
    text: str = Field(min_length=1, max_length=2000)


class Back(_Command):
    type: Literal["back"]


class Home(_Command):
    type: Literal["home"]


class Recents(_Command):
    type: Literal["recents"]


class Notifications(_Command):
    type: Literal["notifications"]


class QuickSettings(_Command):
    type: Literal["quick_settings"]


class PowerDialog(_Command):
    type: Literal["power_dialog"]


class LockScreen(_Command):
    type: Literal["lock_screen"]


class ToggleSplitScreen(_Command):
    type: Literal["toggle_split_screen"]


class DismissShade(_Command):
    type: Literal["dismiss_shade"]


class Launch(_Command):
    type: Literal["launch"]
    package: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$", max_length=200)


class Wait(_Command):
    type: Literal["wait"]
    duration_ms: int = Field(ge=0, le=5000)


class MoveOverlay(_Command):
    type: Literal["move_overlay"]
    x: int = Field(ge=0, le=10000)
    y: int = Field(ge=0, le=10000)


class End(_Command):
    type: Literal["end"]


class EndReturn(_Command):
    type: Literal["end_return"]


class UserAction(_Command):
    type: Literal["user_action"]
    instruction: str = Field(min_length=1, max_length=200)


class Risk(_Command):
    type: Literal["risk"]
    message: str = Field(min_length=1, max_length=200)


class Refuse(_Command):
    type: Literal["refuse"]
    message: str = Field(min_length=1, max_length=200)


class Ask(_Command):
    type: Literal["ask"]
    question: str = Field(min_length=1, max_length=200)


class Camera(_Command):
    type: Literal["camera"]


class Scan(_Command):
    type: Literal["scan"]


class HomePage(_Command):
    type: Literal["home_page"]
    direction: Literal["left", "right"]


Command = Annotated[
    Union[
        Tap, LongPress, Swipe, Zoom, InputText, Back, Home, Recents, Notifications,
        QuickSettings, PowerDialog, LockScreen, ToggleSplitScreen, DismissShade,
        Launch, Wait, MoveOverlay, End, EndReturn, UserAction, Risk, Refuse, Ask,
        Camera, Scan, HomePage,
    ],
    Field(discriminator="type"),
]

parse_command = TypeAdapter(Command).validate_python
