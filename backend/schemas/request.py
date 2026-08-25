from pydantic import BaseModel, field_validator, model_validator


class ElementDTO(BaseModel):
    """접근성 서비스가 추출한 화면 노드 1개. 요청 처리 중에만 메모리에 존재하며 저장하지 않는다."""

    id: int
    text: str | None = None
    content_description: str | None = None
    class_name: str
    clickable: bool
    # 아래 셋은 선택 필드다 — 기존 Android 클라이언트가 안 보내도 요청이 깨지지 않는다.
    editable: bool = False  # ACTION_SET_TEXT 가능 여부
    scrollable: bool = False  # 스크롤 컨테이너 여부
    password: bool = False  # 비밀번호 필드 — text는 보내지 말 것
    bounds: list[int]  # [left, top, right, bottom]

    @field_validator("bounds")
    @classmethod
    def validate_bounds(cls, value: list[int]) -> list[int]:
        if len(value) != 4:
            raise ValueError("bounds must contain exactly 4 integers")
        left, top, right, bottom = value
        # 좌표가 역전된 것(진짜 손상된 데이터)만 거절한다.
        # 면적 0(접힌 뷰·화면 밖 노드)은 실제 UI Tree에 흔히 섞여 들어오므로 허용하고
        # services/rules.py의 필터가 걸러낸다. 노드 하나 때문에 요청 전체가 422가 되면
        # 그 화면에서 자동화가 통째로 멈춘다.
        if left > right:
            raise ValueError("bounds requires left <= right")
        if top > bottom:
            raise ValueError("bounds requires top <= bottom")
        return value

    @property
    def label(self) -> str:
        """사람이 읽을 수 있는 노드 라벨. 게이트·규칙이 노드를 식별할 때 쓴다."""
        return (self.text or self.content_description or "").strip()


class InstalledApp(BaseModel):
    """기기에 설치된 앱 하나. app_package가 없을 때(=아직 대상 앱 미실행) LLM이 여기서 고른다.

    목록은 기기가 준다 — 서버도 프롬프트도 어떤 앱이 있는지 미리 알지 못한다.
    """

    package: str
    label: str


class HistoryEntry(BaseModel):
    """이전 step에서 에이전트가 무엇을 선택했는지에 대한 요약. LLM에 최근 몇 개만 전달한다."""

    step: int
    selected_text: str


class DecideRequest(BaseModel):
    session_id: str
    goal: str
    # None = 아직 대상 앱을 실행하지 않은 상태(첫 스텝). 이때만 elements가 비어도 된다.
    app_package: str | None = None
    elements: list[ElementDTO]
    # app_package가 None일 때만 보낸다. 어떤 앱을 열지 LLM이 여기서 고른다.
    installed_apps: list[InstalledApp] | None = None
    # 사용자의 음성 응답(STT 결과). 확인 질문에 대한 답변 등 대화 턴에서만 채워진다.
    user_speech: str | None = None
    history: list[HistoryEntry] | None = None

    @model_validator(mode="after")
    def validate_elements_presence(self) -> "DecideRequest":
        # 앱이 실행된 상태라면 화면 요소가 반드시 있어야 한다.
        if self.app_package and not self.elements:
            raise ValueError("elements must not be empty when app_package is set")
        return self
