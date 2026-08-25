package com.example.pathpilot.model

import com.google.gson.annotations.SerializedName

/**
 * 백엔드 `/api/v1/decide` 계약과 1:1 대응하는 DTO들. 정본: `dumps/API_SPEC.md` (v2.0),
 * 구현체: `backend/schemas/request.py`, `backend/schemas/response.py`.
 *
 * CLAUDE.md §5 규칙: id는 Int(문자열 아님), camelCase가 아니라 snake_case로 통일한다.
 * 여기 필드명은 Gson이 그대로 JSON key로 직렬화/역직렬화하므로 Kotlin 관례(camelCase)를
 * 따르지 않고 backend 필드명과 문자 그대로 맞춘다.
 * 스키마를 바꿀 때는 반드시 백엔드 담당자와 먼저 맞추고 여기도 같이 고칠 것
 * (docs/ARCHITECTURE.md §2 "공용 파일" 규칙).
 */

/** 접근성 서비스가 화면에서 읽어온 UI 노드 하나. bounds = [left, top, right, bottom]. */
data class ElementDTO(
    val id: Int,
    val text: String?,
    val content_description: String?,
    val class_name: String,
    val clickable: Boolean,
    /** ACTION_SET_TEXT 가능 여부. 서버가 입력 대상 노드를 고를 때 쓴다. */
    val editable: Boolean = false,
    /** 스크롤 컨테이너 여부. 찾는 항목이 화면 밖일 때 서버가 스크롤을 지시할 근거. */
    val scrollable: Boolean = false,
    /** 비밀번호 입력 필드. true면 text는 보내지 않는다 (CLAUDE.md §4-4). */
    val password: Boolean = false,
    val bounds: List<Int>,
)

/**
 * 기기에 설치된 앱 하나. app_package가 null일 때만 함께 보내며, 서버가 이 중에서 고른다.
 *
 * `package`는 Kotlin 예약어라 프로퍼티명으로 못 써서 이 DTO에서만 @SerializedName을 쓴다.
 * JSON key는 그대로 "package"이므로 백엔드 스키마와 일치한다.
 */
data class InstalledApp(
    @SerializedName("package") val packageName: String,
    val label: String,
)

/** 세션 history 한 스텝. */
data class HistoryEntry(
    val step: Int,
    val selected_text: String,
)

/** POST /api/v1/decide 요청 바디. */
data class DecideRequest(
    val session_id: String,
    val goal: String,
    /** null = 아직 대상 앱을 실행하지 않은 상태(첫 스텝). 이때만 elements가 비어도 된다. */
    val app_package: String?,
    val elements: List<ElementDTO>,
    /** app_package가 null일 때만 채운다. 서버가 이 목록에서 열 앱을 고른다. */
    val installed_apps: List<InstalledApp>? = null,
    /** ASK_USER에 대한 예/아니오류 응답만 여기 담는다 — goal에 이어붙이지 않는다 (CLAUDE.md §5-1). */
    val user_speech: String? = null,
    /** 보통 null로 둔다 — 서버가 session_id로 자체 조회한다. */
    val history: List<HistoryEntry>? = null,
)

/** POST /api/v1/decide 응답 바디. */
data class DecideResponse(
    val target_node_id: Int?,
    /** "CLICK" | "SET_TEXT" | "LAUNCH_APP". */
    val action_type: String?,
    /** SET_TEXT일 때 입력할 문자열 / LAUNCH_APP일 때 실행할 패키지명. */
    val input_value: String?,
    /** 로그/디버깅용 요약 — 사용자에게 읽어주지 말 것. */
    val instruction: String,
    /** TTS로 읽어줄 문구. 비어 있을 수 있음("") — 그럴 땐 재생하지 않는다. */
    val voice_message: String,
    val confidence: Double,
    val status: String,
    val reason: String?,
)

/** [DecideResponse.status] 값 상수. */
object DecideStatus {
    const val CONTINUE = "CONTINUE"
    const val DONE = "DONE"
    /** voice_message에 되물을 질문이 담겨 있다. 답변은 상황에 따라 goal 또는 user_speech로 (§5-1). */
    const val ASK_USER = "ASK_USER"
    const val UNSUPPORTED = "UNSUPPORTED"
}

/** [DecideResponse.action_type] 값 상수. */
object ActionType {
    const val CLICK = "CLICK"
    const val SET_TEXT = "SET_TEXT"
    /** target_node_id 없이 input_value의 패키지를 실행한다. */
    const val LAUNCH_APP = "LAUNCH_APP"
}
