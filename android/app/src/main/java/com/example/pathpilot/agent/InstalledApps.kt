package com.example.pathpilot.agent

import android.content.Context
import android.content.Intent
import com.example.pathpilot.model.InstalledApp

/**
 * 런처에 뜨는(=사용자가 열 수 있는) 앱 목록을 모은다.
 *
 * **AndroidManifest에 `<queries>` 선언이 없으면 Android 11+에서 빈 목록이 돌아온다.**
 * 그러면 서버가 열 앱을 고를 수 없어 첫 스텝부터 실패한다 — 가장 빠뜨리기 쉬운 함정이다.
 */
object InstalledApps {

    fun list(context: Context): List<InstalledApp> {
        val pm = context.packageManager
        val launcherIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)

        return pm.queryIntentActivities(launcherIntent, 0)
            .asSequence()
            .mapNotNull { resolveInfo ->
                val packageName = resolveInfo.activityInfo?.packageName ?: return@mapNotNull null
                // 우리 앱 자신은 후보에서 뺀다 — AI가 우리를 다시 열 이유가 없다.
                if (packageName == context.packageName) return@mapNotNull null
                InstalledApp(
                    packageName = packageName,
                    label = resolveInfo.loadLabel(pm).toString(),
                )
            }
            .distinctBy { it.packageName }
            .sortedBy { it.label }
            .toList()
    }
}
