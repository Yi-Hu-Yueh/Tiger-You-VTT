package tw.tiger.tigeryouvtt.standalone

import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** SDK wiring contracts; Android's actual permission UI still requires owner device testing.
 * PlaybackTest supplies executable consent-state, PCM, STOP and transcript regression coverage.
 */
class PlaybackUxTest {
    private val root = File("src/main/java/tw/tiger/tigeryouvtt/standalone")
    private fun source(name: String) = File(root, name).readText()
    private fun start() = source("MainActivity.kt").substringAfter("fun startPlayback() {")
        .substringBefore("fun startMicrophone() {")

    @Test fun api34PlusUsesOfficialDefaultDisplayConfiguration() {
        assertTrue(start().contains("val consentIntent = if (Build.VERSION.SDK_INT >= 34) {"))
        val modern = start().substringAfter("if (Build.VERSION.SDK_INT >= 34) {").substringBefore("} else {")
        assertTrue(modern.contains("manager.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())"))
        assertFalse(modern.contains("createConfigForUserChoice"))
        assertFalse(modern.contains("manager.createScreenCaptureIntent()"))
    }

    @Test fun api29Through33RetainsLegacySystemConsentWithoutNewApiReferences() {
        val legacy = start().substringAfter("} else {").substringBefore("}")
        assertTrue(legacy.contains("manager.createScreenCaptureIntent()"))
        assertFalse(legacy.contains("MediaProjectionConfig"))
    }

    @Test fun exactlyOneSystemLaunchAfterModelAndPendingRequestChecks() {
        val start = start()
        assertEquals(1, Regex("projectionConsent\\.launch\\(").findAll(start).count())
        assertTrue(start.contains("projectionConsent.launch(consentIntent)"))
        assertTrue(start.indexOf("playbackStartError(") < start.indexOf("val consentIntent"))
        assertTrue(start.indexOf("if (projectionRequest >= 0)") < start.indexOf("val consentIntent"))
        assertTrue(start.indexOf("PlaybackJobs.request(") < start.indexOf("val consentIntent"))
        assertTrue(start.contains("projectionRequest = -1; PlaybackJobs.fail("))
    }

    @Test fun noTigerAppChooserOrSecurityBypassAdded() {
        val code = root.walkTopDown().filter { it.extension == "kt" }.joinToString("\n") { it.readText() }
        listOf("Intent.createChooser", "queryIntentActivities", "getInstalledApplications",
            "createConfigForUserChoice", "AccessibilityService", "getDeclaredMethod", "setAccessible(")
            .forEach { assertFalse(it, code.contains(it)) }
        assertFalse(File("src/main/AndroidManifest.xml").readText().contains("android.permission.QUERY_ALL_PACKAGES"))
    }

    @Test fun wholeDisplayAuthorizationStillHasNoVisualCaptureOrMicrophoneFallback() {
        val playback = source("PlaybackService.kt") + source("PlaybackAudioSource.kt") + start()
        listOf("createVirtualDisplay(", "ImageReader", "MediaRecorder", "MediaCodec", "setAudioSource(")
            .forEach { assertFalse(it, playback.contains(it)) }
        assertTrue(playback.contains("setAudioPlaybackCaptureConfig(capture)"))
        assertFalse(playback.contains("AudioRecordSource.open"))
    }

    @Test fun consentAndNeutralDeviceDependentUiRemainExplicit() {
        val ui = source("MainActivity.kt")
        assertTrue(ui.contains("每次開始都需要 Android 系統授權"))
        assertTrue(ui.contains("部分裝置仍可能顯示選擇畫面"))
        assertFalse(ui.contains("只會顯示一個對話框"))
        assertTrue(ui.contains("PlaybackJobs.result(request, result.resultCode == RESULT_OK && result.data != null)"))
        assertTrue(source("PlaybackService.kt").contains("consent.consume("))
        assertTrue(source("PlaybackService.kt").contains("intent.removeExtra(\"projection-data\")"))
    }
}
