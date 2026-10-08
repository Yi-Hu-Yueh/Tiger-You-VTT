package tw.tiger.tigeryouvtt.standalone

import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun YouTubeScreen(traditional: Boolean) {
    val context = LocalContext.current
    val state by YouTubeJobs.state.collectAsState()
    var query by rememberSaveable { mutableStateOf("") }
    var url by rememberSaveable { mutableStateOf("") }
    var notice by remember { mutableStateOf("") }
    var export by remember { mutableStateOf("") }
    var license by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val exporter = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/plain")) { uri ->
        if (uri != null) scope.launch {
            notice = withContext(Dispatchers.IO) {
                runCatching { context.contentResolver.openOutputStream(uri, "wt").use { requireNotNull(it).write(export.toByteArray(Charsets.UTF_8)) }; "已匯出" }
                    .getOrDefault("匯出失敗，請重新選擇儲存位置")
            }
        }
    }
    fun begin(action: String, selected: String = url) {
        notice = ""
        try {
            val request = Intent(context, YouTubeService::class.java).setAction(action).putExtra("traditional", traditional)
            if (action == "search") request.putExtra("query", youtubeQuery(query))
            else request.putExtra("url", "https://www.youtube.com/watch?v=${youtubeId(selected)}")
            context.startForegroundService(request)
        } catch (e: YouTubeFailure) { notice = e.userMessage
        } catch (_: Exception) { notice = "無法啟動 YouTube 工作，請保持 App 開啟後重試" }
    }
    Text("網路僅用於取得 YouTube 資料；語音辨識仍在本機完成。")
    Text("本機檔案、麥克風、裝置音訊與內建模型仍可離線使用。YouTube 不需 PC、API 金鑰或 Google 登入。")
    TextButton(onClick = { scope.launch { license = withContext(Dispatchers.IO) {
        runCatching { context.assets.open("licenses/NewPipe-GPL-3.0.txt").bufferedReader().use { it.readText() } }.getOrDefault("無法讀取授權文件")
    } } }) { Text("NewPipe Extractor v0.26.5 — GPL-3.0-or-later") }
    OutlinedTextField(query, { query = it }, label = { Text("YouTube 搜尋") }, enabled = !state.busy, modifier = Modifier.fillMaxWidth())
    Button(enabled = !state.busy, onClick = { begin("search") }) { Text("搜尋") }
    state.results.forEach { item ->
        OutlinedButton(enabled = !state.busy, onClick = { url = item.url; begin("inspect", item.url) }, modifier = Modifier.fillMaxWidth()) {
            Column { Text(item.title); Text("${item.channel}｜${if (item.seconds > 0) "${item.seconds} 秒" else "長度未知／直播"}") }
        }
    }
    OutlinedTextField(url, { url = it }, label = { Text("YouTube 來源網址") }, enabled = !state.busy, modifier = Modifier.fillMaxWidth())
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(enabled = !state.busy, onClick = { begin("inspect") }) { Text("解析影片") }
        Button(enabled = !state.busy, onClick = { begin("retrieve") }) { Text("取得字幕") }
        OutlinedButton(enabled = state.busy, onClick = { YouTubeJobs.stop() }) { Text("停止") }
    }
    if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
    Text(state.status, color = if (state.error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
    if (state.diagnostic.isNotBlank()) Text("下載診斷（不含網址／憑證）：${state.diagnostic}")
    if (notice.isNotEmpty()) Text(notice)
    state.video?.let { video ->
        Text("標題：${video.item.title}\n頻道：${video.item.channel}\n長度：${video.item.seconds} 秒\n影片 ID：${video.item.id}")
        Text("可用字幕：${video.captions.size} 軌\n字幕來源／處理方式：${state.provenance}")
    }
    Text("優先取得 zh-TW → zh → en 字幕；無可用字幕時下載 M4A 音訊，再使用本機 SenseVoice。驗證受限影片不支援登入；可自行改用裝置音訊。")
    Text("暫存音訊限 100 MiB／影片限 60 分鐘；完成或取消後清理。字幕可匯出；本頁字幕在本次 App 執行期間保留。")
    if (state.metrics.isNotEmpty()) Text(state.metrics)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        listOf("txt", "vtt", "srt").forEach { format ->
            OutlinedButton(enabled = !state.busy && state.segments.isNotEmpty(), onClick = {
                export = Export.render(state.segments, format); exporter.launch("Tiger-YouTube.$format")
            }) { Text(format.uppercase()) }
        }
    }
    Text("保留字幕：${state.segments.size} 段（新結果出現前保留前次字幕）")
    state.segments.takeLast(30).forEach { Text("${Export.timestamp(it.start, '.')}\n${it.text}") }
    license?.let { text -> AlertDialog(onDismissRequest = { license = null }, title = { Text("NewPipe 授權（離線）") },
        text = { Text(text, modifier = Modifier.verticalScroll(rememberScrollState())) },
        confirmButton = { TextButton(onClick = { license = null }) { Text("關閉") } }) }
}
