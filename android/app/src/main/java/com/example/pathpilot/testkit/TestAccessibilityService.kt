package com.example.pathpilot.testkit

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.example.pathpilot.agent.AgentSession
import com.example.pathpilot.model.ActionType
import com.example.pathpilot.model.DecideRequest
import com.example.pathpilot.model.DecideResponse
import com.example.pathpilot.model.DecideStatus
import com.example.pathpilot.model.ElementDTO
import com.example.pathpilot.network.RetrofitClient
import com.example.pathpilot.overlay.StatusOverlayManager
import com.example.pathpilot.voice.VoiceInteractionManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 앱이 열린 뒤의 화면 조작 루프를 담당한다 — 화면 읽기 → /decide 호출 → 클릭/입력 → 반복.
 * 발화 수집과 앱 실행은 MainActivity가 맡고, 목표는 [AgentSession]으로 넘어온다.
 *
 * 대상 앱을 코드가 정하지 않는다. 서버가 LAUNCH_APP으로 지정한 앱에서만 동작하므로
 * 이 파일에는 어떤 앱 이름도 등장하지 않는다 (CLAUDE.md §12).
 *
 * **이건 정식 구현이 아니다.** 멤버 C가 `service/` 아래에 정식 AccessibilityService를 만들면
 * 이 파일과 `res/xml/test_accessibility_service_config.xml`, Manifest의 관련 `<service>`
 * 블록을 지운다 (docs/ARCHITECTURE.md §2).
 *
 * 알려진 한계 (테스트 용도라 감수):
 * - [nodeMap]에 담아둔 AccessibilityNodeInfo는 서버 응답이 오는 사이 화면이 바뀌면 무효화될 수
 *   있다. performAction이 조용히 실패하면 이게 원인일 가능성이 높다.
 * - 목표 문장과 세션은 [AgentSession]에서 받는다. MainActivity에서 사용자가 말하고,
 *   서버가 LAUNCH_APP으로 지정한 앱이 열린 뒤부터 이 서비스가 이어받는다.
 */
class TestAccessibilityService : AccessibilityService() {

    private lateinit var voice: VoiceInteractionManager
    private lateinit var overlay: StatusOverlayManager

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val debounceHandler = Handler(Looper.getMainLooper())
    private var pendingCollect: Runnable? = null

    private var isRequestInFlight = false

    /** 이번 스텝에서 화면을 훑을 때 부여한 id -> 실제 노드. §알려진 한계 참고. */
    private val nodeMap = mutableMapOf<Int, AccessibilityNodeInfo>()

    override fun onServiceConnected() {
        super.onServiceConnected()
        voice = VoiceInteractionManager(this)
        overlay = StatusOverlayManager(this)
        Log.i(TAG, "TestAccessibilityService connected")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // 사용자가 발화하지 않았으면 아무 화면에도 개입하지 않는다.
        val goal = AgentSession.goal ?: return

        // 서버가 LAUNCH_APP으로 지정한 앱의 화면에서만 동작한다.
        // 대상 앱을 코드가 정하지 않으므로 여기에 앱 이름이 등장하지 않는다.
        val eventPackage = event?.packageName?.toString() ?: return
        if (eventPackage != AgentSession.targetPackage) return

        overlay.showOrUpdate(goal)
        scheduleCollectAndDecide()
    }

    override fun onInterrupt() {
        Log.w(TAG, "TestAccessibilityService interrupted")
    }

    override fun onDestroy() {
        super.onDestroy()
        pendingCollect?.let { debounceHandler.removeCallbacks(it) }
        serviceScope.cancel()
        overlay.hide()
        voice.shutdown()
    }

    /** 화면 변경 이벤트가 연속으로 들어와도 마지막 한 번만 처리한다 (디바운스). */
    private fun scheduleCollectAndDecide() {
        pendingCollect?.let { debounceHandler.removeCallbacks(it) }
        val runnable = Runnable { collectAndDecide(userSpeech = null) }
        pendingCollect = runnable
        debounceHandler.postDelayed(runnable, DEBOUNCE_MS)
    }

    /** 현재 화면을 ElementDTO 목록으로 만들어 /decide를 호출한다. */
    private fun collectAndDecide(userSpeech: String?) {
        if (isRequestInFlight) return
        val root = rootInActiveWindow ?: return

        nodeMap.clear()
        val elements = mutableListOf<ElementDTO>()
        var nextId = 1

        fun visit(node: AccessibilityNodeInfo) {
            // 비밀번호 필드의 내용은 서버로 보내지 않는다 (CLAUDE.md §4-4).
            val text = if (node.isPassword) null else node.text?.toString()
            val description = node.contentDescription?.toString()
            if (node.isClickable || !text.isNullOrBlank() || !description.isNullOrBlank()) {
                val bounds = Rect()
                node.getBoundsInScreen(bounds)
                // 아직 레이아웃이 안 잡힌 노드는 bounds가 [0,0,0,0] 등 폭/높이 0으로 나온다.
                // 백엔드가 bounds 하나라도 잘못되면 요청 전체를 422로 거부하므로 여기서 미리 거른다.
                val hasValidBounds = bounds.left < bounds.right && bounds.top < bounds.bottom
                if (hasValidBounds) {
                    val id = nextId++
                    nodeMap[id] = node
                    elements.add(
                        ElementDTO(
                            id = id,
                            text = text,
                            content_description = description,
                            class_name = node.className?.toString() ?: "unknown",
                            clickable = node.isClickable,
                            editable = node.isEditable,
                            scrollable = node.isScrollable,
                            password = node.isPassword,
                            bounds = listOf(bounds.left, bounds.top, bounds.right, bounds.bottom),
                        ),
                    )
                }
            }
            for (i in 0 until node.childCount) {
                node.getChild(i)?.let { visit(it) }
            }
        }
        visit(root)

        if (elements.isEmpty()) return

        isRequestInFlight = true
        overlay.showOrUpdate("화면 분석 중… (${elements.size}개 요소)")

        val request = DecideRequest(
            session_id = AgentSession.sessionId,
            goal = AgentSession.goal ?: return,
            app_package = AgentSession.targetPackage,
            elements = elements,
            user_speech = userSpeech,
        )

        serviceScope.launch {
            try {
                val response = withContext(Dispatchers.IO) {
                    RetrofitClient.apiService.decide(request)
                }
                handleResponse(response)
            } catch (e: Exception) {
                Log.e(TAG, "decide 호출 실패", e)
                overlay.showOrUpdate("서버 호출 실패: ${e.message}")
            } finally {
                isRequestInFlight = false
            }
        }
    }

    private fun handleResponse(response: DecideResponse) {
        if (response.voice_message.isNotBlank()) {
            voice.speak(response.voice_message)
        }

        when (response.status) {
            DecideStatus.CONTINUE -> {
                overlay.showOrUpdate(response.voice_message.ifBlank { "다음 동작 실행 중" })
                performTargetAction(response)
                // 클릭/입력 후 화면이 바뀌면 onAccessibilityEvent가 다시 스케줄링한다.
            }

            DecideStatus.ASK_USER -> {
                overlay.showOrUpdate("답변 대기: ${response.voice_message}")
                voice.listenOnce(
                    onResult = { answer -> collectAndDecide(userSpeech = answer) },
                    onError = { err -> overlay.showOrUpdate("답변 인식 실패: $err") },
                )
            }

            DecideStatus.DONE -> {
                overlay.showOrUpdate("완료: ${response.voice_message}")
                AgentSession.finish()
            }

            DecideStatus.UNSUPPORTED -> {
                overlay.showOrUpdate("중단됨: ${response.reason ?: response.voice_message}")
                AgentSession.finish()
            }
        }
    }

    private fun performTargetAction(response: DecideResponse) {
        if (response.action_type == ActionType.LAUNCH_APP) {
            // 서버가 다른 앱으로 넘기려는 경우(잘못된 앱이 열렸을 때 등).
            response.input_value?.let(::launchApp)
            return
        }

        val node = response.target_node_id?.let { nodeMap[it] }
        if (node == null || response.action_type == null) {
            Log.w(TAG, "target node를 찾지 못함 (target_node_id=${response.target_node_id})")
            return
        }
        when (response.action_type) {
            ActionType.CLICK -> node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            ActionType.SET_TEXT -> {
                val args = Bundle().apply {
                    putCharSequence(
                        AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                        response.input_value ?: "",
                    )
                }
                node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            }
        }
    }

    /** 서버가 지정한 앱을 실행한다. 어떤 앱인지는 서버가 정한다. */
    private fun launchApp(packageName: String) {
        val intent = packageManager.getLaunchIntentForPackage(packageName)
        if (intent == null) {
            Log.w(TAG, "실행할 수 없는 패키지: $packageName")
            overlay.showOrUpdate("그 앱을 열 수 없어요.")
            AgentSession.finish()
            return
        }
        AgentSession.targetPackage = packageName
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivity(intent)
    }

    companion object {
        private const val TAG = "TestA11yService"
        private const val DEBOUNCE_MS = 500L
    }
}
