package tw.tiger.tigeryouvtt

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModelProvider

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val model = ViewModelProvider(this)[TigerViewModel::class.java]
        setContent {
            MaterialTheme(colorScheme = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()) {
                Surface(Modifier.fillMaxSize()) { TigerApp(model) }
            }
        }
    }
}

@Composable
private fun TigerApp(model: TigerViewModel) {
    val ui = model.ui
    var tab by rememberSaveable { mutableIntStateOf(3) }
    var query by rememberSaveable { mutableStateOf("") }
    var sort by rememberSaveable { mutableStateOf("relevance") }
    var url by rememberSaveable { mutableStateOf("") }
    var start by rememberSaveable { mutableStateOf("0:0") }
    var end by rememberSaveable { mutableStateOf("0:10") }
    var diarize by rememberSaveable { mutableStateOf(false) }
    var low by rememberSaveable { mutableStateOf(false) }
    var device by rememberSaveable { mutableStateOf<Int?>(null) }
    val saveDocument = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/plain")) { uri ->
        if (uri != null) model.export(uri, model.pendingExport)
    }
    val videoPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) model.picked(uri, "video") }
    val audioPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) model.picked(uri, "audio") }
    val ready = !ui.busy && !ui.loading
    val settingsReady = !ui.loading && (!ui.busy || ui.connectionLost)
    Column(Modifier.fillMaxSize().safeDrawingPadding().imePadding()) {
        Text("Tiger-You-VTT", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(16.dp))
        Row(Modifier.fillMaxWidth()) {
            listOf("搜尋", "來源", "電腦音訊", "設定").forEachIndexed { index, name ->
                TextButton(onClick = { tab = index }, modifier = Modifier.weight(1f)) { Text(if (tab == index) "• $name" else name) }
            }
        }
        HorizontalDivider()
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (ui.loading || ui.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            Text(ui.message, color = MaterialTheme.colorScheme.primary)
            when (tab) {
                0 -> {
                    Text("YouTube 搜尋", style = MaterialTheme.typography.titleMedium)
                    Field("關鍵字", query, { query = it }, ready)
                    Choice("排序", listOf("relevance" to "相關度", "upload_date" to "最新上傳", "view_count" to "觀看次數", "duration" to "影片長度"), sort, ready) { sort = it }
                    Button(onClick = { model.search(query, sort) }, enabled = ready && query.isNotBlank()) { Text("搜尋") }
                    Toggle("啟用說話者分離", diarize, ready) { diarize = it }
                    ui.videos.forEach { video ->
                        Card(Modifier.fillMaxWidth()) {
                            Row(Modifier.padding(8.dp)) {
                                Checkbox(video.id in ui.selected, { model.select(video.id, it) }, enabled = ready)
                                Column(Modifier.weight(1f)) { Text(video.title); Text(video.details, style = MaterialTheme.typography.bodySmall) }
                            }
                        }
                    }
                    Button(onClick = { model.startSearch(diarize) }, enabled = ready && ui.selected.isNotEmpty()) { Text("開始取得字幕（依序）") }
                }
                1 -> {
                    Text("來源", style = MaterialTheme.typography.titleMedium)
                    Field("YouTube 網址", url, { url = it }, ready)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(start, { start = it }, label = { Text("開始 H:M") }, modifier = Modifier.weight(1f), enabled = ready, singleLine = true)
                        OutlinedTextField(end, { end = it }, label = { Text("結束 H:M") }, modifier = Modifier.weight(1f), enabled = ready, singleLine = true)
                    }
                    Text("預設 0:0 → 0:10（前 10 分鐘）；同樣套用於上傳檔案。", style = MaterialTheme.typography.bodySmall)
                    Toggle("啟用說話者分離", diarize, ready) { diarize = it }
                    Button(onClick = { model.startUrl(url, start, end, diarize) }, enabled = ready && url.isNotBlank()) { Text("開始 YouTube 字幕") }
                    HorizontalDivider()
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = { videoPicker.launch(arrayOf("video/*")) }, enabled = ready) { Text("選擇影片") }
                        OutlinedButton(onClick = { audioPicker.launch(arrayOf("audio/*")) }, enabled = ready) { Text("選擇語音檔") }
                    }
                    Text(ui.fileName)
                    Button(onClick = { model.startFile(start, end, diarize) }, enabled = ready && ui.fileUri != null) { Text("上傳並開始") }
                }
                2 -> {
                    Text("電腦播放聲音（PC）", style = MaterialTheme.typography.titleMedium)
                    Text("控制 Windows PC 的 WASAPI Loopback，不是擷取手機音訊。")
                    OutlinedButton(onClick = model::loadDevices, enabled = ready) { Text("載入 PC 音訊裝置") }
                    val selected = device?.takeIf { id -> ui.devices.any { it.id == id } } ?: ui.devices.firstOrNull { it.default }?.id ?: ui.devices.firstOrNull()?.id
                    Choice("裝置", ui.devices.map { it.id.toString() to it.name }, selected?.toString() ?: "", ready) { device = it.toInt() }
                    Toggle("低延遲即時字幕", low, ready) { low = it }
                    Text(if (low) "PC 使用 large-v3-turbo；暖機後才收音，4 秒視窗，非零延遲。" else "一般模式：保留原有 10 秒 System Audio 流程。", style = MaterialTheme.typography.bodySmall)
                    Button(onClick = { selected?.let { model.startPc(it, low) } }, enabled = ready && selected != null) { Text("開始電腦字幕") }
                }
                3 -> {
                    Text("設定", style = MaterialTheme.typography.titleMedium)
                    Field("伺服器網址", model.server, { model.server = it }, settingsReady)
                    Text("例如 http://192.168.1.100:8000；不要使用 PC 的 127.0.0.1。", style = MaterialTheme.typography.bodySmall)
                    OutlinedTextField(model.token, { model.token = it }, label = { Text("連線金鑰") }, visualTransformation = PasswordVisualTransformation(), singleLine = true, enabled = settingsReady, modifier = Modifier.fillMaxWidth())
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = model::save, enabled = settingsReady) { Text("儲存") }
                        OutlinedButton(onClick = model::testConnection, enabled = ready) { Text("測試連線") }
                    }
                    Text("測試版僅供手機與 PC 在受信任的私人 LAN 使用。HTTP 未加密，連線金鑰不能防止惡意 LAN 監聽。Whisper / CUDA / pyannote 都在 PC 執行。")
                    Text("不支援手機麥克風串流或手機系統音訊。請保持 App 開啟；離線／關閉 App 不代表 PC 工作已停止。")
                }
            }
            if (ui.cards.isNotEmpty()) {
                HorizontalDivider()
                Text("字幕工作", style = MaterialTheme.typography.titleMedium)
                ui.cards.forEachIndexed { index, card ->
                    TranscriptCard(card) { ext, text ->
                        model.pendingExport = text
                        saveDocument.launch("Tiger-You-VTT-${index + 1}.$ext")
                    }
                }
            }
        }
        if (ui.busy) {
            Button(onClick = model::stop, enabled = !ui.stopping, modifier = Modifier.fillMaxWidth().padding(12.dp)) {
                Text(if (ui.stopping) "停止中（待 PC 確認）" else "STOP / 取消上傳")
            }
        }
    }
}

@Composable private fun Field(label: String, value: String, change: (String) -> Unit, enabled: Boolean) {
    OutlinedTextField(value, change, label = { Text(label) }, singleLine = true, enabled = enabled, modifier = Modifier.fillMaxWidth())
}
@Composable private fun Toggle(label: String, value: Boolean, enabled: Boolean, change: (Boolean) -> Unit) {
    Row { Checkbox(value, change, enabled = enabled); Text(label, Modifier.padding(top = 12.dp)) }
}
@Composable private fun Choice(label: String, options: List<Pair<String, String>>, selected: String, enabled: Boolean, change: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { expanded = true }, enabled = enabled && options.isNotEmpty()) { Text("$label：${options.firstOrNull { it.first == selected }?.second ?: "尚未選擇"}") }
        DropdownMenu(expanded, { expanded = false }) {
            options.forEach { (key, value) -> DropdownMenuItem(text = { Text(value) }, onClick = { change(key); expanded = false }) }
        }
    }
}
@Composable private fun TranscriptCard(card: Card, export: (String, String) -> Unit) {
    var speakerAware by rememberSaveable(card.title) { mutableStateOf(true) }
    val snapshot = card.snapshot
    val transcript = snapshot?.selected(speakerAware)
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(card.title, style = MaterialTheme.typography.titleSmall)
            Text(statusLabel(card.state, snapshot?.phase ?: ""))
            if (snapshot != null) Text("${snapshot.elapsed.toInt()} 秒 · ${snapshot.count} 個片段")
            snapshot?.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            if (snapshot?.speakers != null) Toggle("顯示／匯出 Speaker 字幕（取消為原始字幕）", speakerAware, true) { speakerAware = it }
            SelectionContainer { Text(transcript?.txt ?: "等待字幕...", Modifier.heightIn(max = 280.dp).verticalScroll(rememberScrollState())) }
            Row {
                listOf("txt", "vtt", "srt").forEach { ext ->
                    val text = transcript?.format(ext) ?: ""
                    TextButton(onClick = { export(ext, text) }, enabled = text.isNotBlank()) { Text("儲存 ${ext.uppercase()}") }
                }
            }
        }
    }
}
