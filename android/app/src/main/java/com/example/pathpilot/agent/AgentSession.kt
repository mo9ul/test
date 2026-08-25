package com.example.pathpilot.agent

import java.util.UUID

/**
 * MainActivity(음성 입력)와 AccessibilityService(화면 조작) 사이에서 목표를 전달하는 상태.
 *
 * 둘은 같은 프로세스에 있지만 서로를 직접 참조할 수 없다 — 서비스는 시스템이 띄우고
 * Activity는 사용자가 띄우기 때문이다. 그래서 프로세스 전역 상태로 이어 붙인다.
 *
 * 화면 데이터는 담지 않는다. 목표 문장과 세션 id만 갖는다 (CLAUDE.md §4-1 비영속화).
 */
object AgentSession {

    /** 사용자가 말한 목표. 세션이 없으면 null. */
    @Volatile
    var goal: String? = null
        private set

    /** 서버가 LAUNCH_APP으로 지정해 실제로 실행한 앱. 이 앱의 화면에서만 루프를 돈다. */
    @Volatile
    var targetPackage: String? = null

    /** 서버 세션 키. 되묻기·동의가 같은 세션으로 이어지려면 유지돼야 한다. */
    @Volatile
    var sessionId: String = ""
        private set

    val isActive: Boolean
        get() = goal != null

    /** 새 발화로 세션을 시작한다. 이전 세션 상태는 버린다. */
    @Synchronized
    fun start(spokenGoal: String) {
        goal = spokenGoal
        targetPackage = null
        sessionId = UUID.randomUUID().toString()
    }

    @Synchronized
    fun finish() {
        goal = null
        targetPackage = null
    }
}
