package tw.tiger.tigeryouvtt

import android.app.Application
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okio.BufferedSink
import okio.source
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.URLEncoder

data class Card(val title: String, val snapshot: Snapshot? = null, val state: String = "queued")
data class UiState(
    val busy: Boolean = false, val loading: Boolean = false, val stopping: Boolean = false, val connectionLost: Boolean = false,
    val message: String = "請先到設定輸入 PC 伺服器網址與連線金鑰",
    val videos: List<SearchVideo> = emptyList(), val selected: Set<String> = emptySet(),
    val devices: List<PcDevice> = emptyList(), val cards: List<Card> = emptyList(),
    val fileUri: Uri? = null, val fileName: String = "尚未選擇檔案", val fileKind: String = "video"
)

class TigerViewModel(application: Application) : AndroidViewModel(application) {
    private val preferences = application.getSharedPreferences("connection", 0)
    var server by mutableStateOf(preferences.getString("server", "") ?: "")
    var token by mutableStateOf(preferences.getString("token", "") ?: "")
    var ui by mutableStateOf(UiState())
        private set
    private var runningApi: TigerApi? = null
    private val apis = mutableSetOf<TigerApi>()
    @Volatile private var creationCall: Call? = null
    @Volatile private var stopRequested = false
    private var reconnectRequested = false
    private var activeId: String? = null
    // Keep potentially long exports out of Android's size-limited saved-state Bundle.
    var pendingExport: String = ""

    private fun api(): TigerApi {
        require(token.trim().matches(Regex("[A-Za-z0-9+/=_-]{32,128}"))) { "請輸入 PC 腳本顯示的完整連線金鑰" }
        return TigerApi(server, token.trim()).also { apis.add(it) }
    }
    private fun release(api: TigerApi) { api.close(); apis.remove(api) }
    private fun errorText(error: Exception) = when (error) {
        is ApiFailure -> error.message ?: "伺服器處理失敗"
        is ConnectException -> "伺服器未啟動或無法連線；請檢查 Wi-Fi、IP 與防火牆"
        is SocketTimeoutException -> "連線逾時，請檢查網路後重試"
        is IllegalArgumentException -> error.message ?: "輸入格式錯誤"
        is IOException -> error.message?.takeIf { it.startsWith("金鑰") || it.startsWith("請求") || it.startsWith("伺服器") || it.startsWith("輸入") || it.startsWith("找不到") || it.startsWith("檔案") } ?: "無法連線；請檢查 Wi-Fi、伺服器網址與金鑰後重試"
        else -> "無法處理回應或檔案，請確認伺服器版本與檔案權限後重試"
    }
    private fun operation(action: suspend (TigerApi) -> Unit) {
        if (ui.loading || ui.busy) return
        ui = ui.copy(loading = true, message = "連線中...")
        viewModelScope.launch {
            var connection: TigerApi? = null
            try { connection = api(); action(connection) }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { ui = ui.copy(message = errorText(e)) }
            finally { connection?.let(::release); ui = ui.copy(loading = false) }
        }
    }
    fun save() {
        if (ui.busy && !ui.connectionLost) return
        try {
            server = normalizeUrl(server)
            require(token.isNotBlank()) { "請輸入連線金鑰" }
            token = token.trim()
            preferences.edit().putString("server", server).putString("token", token).apply()
            reconnectRequested = ui.busy
            ui = ui.copy(message = "設定已儲存；HTTP 僅限受信任的私人區域網路")
        } catch (e: Exception) { ui = ui.copy(message = errorText(e)) }
    }
    fun testConnection() = operation { connection ->
        check(connection.request("/health").optString("status") == "ok")
        ui = ui.copy(message = "已連線")
    }
    fun search(query: String, sort: String) = operation { connection ->
        require(query.isNotBlank()) { "請輸入搜尋關鍵字" }
        val results = parseSearch(connection.request("/api/youtube/search?q=${URLEncoder.encode(query, "UTF-8")}&sort=$sort&limit=10"))
        ui = ui.copy(videos = results, selected = results.take(3).map { it.id }.toSet(), message = "找到 ${results.size} 筆影片")
    }
    fun select(id: String, selected: Boolean) {
        if (!ui.busy) ui = ui.copy(selected = if (selected) ui.selected + id else ui.selected - id)
    }
    fun loadDevices() = operation { connection ->
        val array = connection.request("/api/system-audio/devices").getJSONArray("devices")
        val devices = (0 until array.length()).map { array.getJSONObject(it).let { d -> PcDevice(d.getInt("id"), d.getString("name"), d.optBoolean("is_default")) } }
        ui = ui.copy(devices = devices, message = if (devices.isEmpty()) "PC 沒有可用的 WASAPI Loopback 裝置" else "請依裝置名稱選擇 PC 音訊")
    }
    fun picked(uri: Uri, kind: String) {
        viewModelScope.launch {
            try {
                val name = withContext(Dispatchers.IO) {
                    val resolver = getApplication<Application>().contentResolver
                    try { resolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) } catch (_: SecurityException) { /* Provider may grant only temporary access. */ }
                    resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                        if (it.moveToFirst()) it.getString(0) else null
                    } ?: "upload.${if (kind == "video") "mp4" else "mp3"}"
                }
                ui = ui.copy(fileUri = uri, fileName = name, fileKind = kind)
            } catch (e: Exception) { ui = ui.copy(message = errorText(e)) }
        }
    }
    private fun uploadBody(uri: Uri, name: String, start: String, end: String, diarize: Boolean): RequestBody {
        val resolver = getApplication<Application>().contentResolver
        val body = object : RequestBody() {
            override fun contentType() = "application/octet-stream".toMediaTypeOrNull()
            override fun isOneShot() = true
            override fun writeTo(sink: BufferedSink) {
                val stream = resolver.openInputStream(uri) ?: throw IOException("檔案無法開啟，請重新選擇")
                stream.source().use { sink.writeAll(it) }
            }
        }
        return MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("start_time", start).addFormDataPart("end_time", end)
            .addFormDataPart("end_time_is_default", (end == "0:10").toString())
            .addFormDataPart("enable_diarization", diarize.toString())
            .addFormDataPart("file", name.replace("\r", "").replace("\n", ""), body).build()
    }
    private fun updateCard(index: Int, snapshot: Snapshot) {
        ui = ui.copy(cards = ui.cards.mapIndexed { i, card -> if (i == index) card.copy(snapshot = snapshot, state = snapshot.status) else card })
    }
    private fun runJobs(titles: List<String>, create: suspend (TigerApi, Int) -> String) {
        if (ui.busy || ui.loading || titles.isEmpty()) return
        stopRequested = false
        reconnectRequested = false
        ui = ui.copy(busy = true, stopping = false, connectionLost = false, cards = titles.map { Card(it) }, message = "正在建立工作...")
        viewModelScope.launch {
            var connection: TigerApi? = null
            try {
                connection = api(); runningApi = connection
                for (index in titles.indices) {
                    if (stopRequested) break
                    activeId = create(requireNotNull(connection), index)
                    creationCall = null
                    while (true) {
                        try {
                            if (reconnectRequested) {
                                val replacement = api()
                                connection?.let(::release)
                                connection = replacement
                                runningApi = replacement
                                reconnectRequested = false
                            }
                            val connected = requireNotNull(connection)
                            if (stopRequested) connected.stop(activeId!!).also { updateCard(index, it) }
                            val snapshot = Snapshot.parse(connected.request("/api/jobs/${activeId}"))
                            updateCard(index, snapshot)
                            ui = ui.copy(connectionLost = false, message = snapshot.error ?: statusLabel(snapshot.status, snapshot.phase))
                            if (terminal(snapshot.status)) break
                            delay(1000)
                        } catch (e: CancellationException) { throw e }
                        catch (e: Exception) {
                            if (e is ApiFailure && e.statusCode == 404) throw e
                            ui = ui.copy(connectionLost = true, message = errorText(e) + "；保留字幕，5 秒後自動重試。可按 STOP 或到設定修正連線後儲存。")
                            delay(5000)
                        }
                    }
                    activeId = null
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                ui = ui.copy(message = if (stopRequested) "已取消上傳／建立請求；若伺服器已接收但回應遺失，請在 PC 確認工作狀態" else errorText(e),
                    cards = ui.cards.map { card -> if (card.snapshot != null && !terminal(card.snapshot.status))
                        card.copy(state = "failed", snapshot = card.snapshot.copy(status = "failed", error = "工作追蹤中斷；已保留字幕")) else card })
            } finally {
                creationCall = null; activeId = null; runningApi = null
                connection?.let(::release)
                ui = ui.copy(busy = false, stopping = false, connectionLost = false, cards = ui.cards.map {
                    if (it.snapshot == null) it.copy(state = if (stopRequested) "stopped" else "failed") else it
                })
            }
        }
    }
    private fun trackCreation(call: Call) { creationCall = call; if (stopRequested) call.cancel() }
    fun startSearch(diarize: Boolean) {
        val videos = ui.videos.filter { it.id in ui.selected }
        runJobs(videos.map { it.title }) { api, index -> api.post("/api/jobs/youtube", youtubePayload(videos[index].url, diarize = diarize), ::trackCreation).getString("job_id") }
    }
    fun startUrl(url: String, start: String, end: String, diarize: Boolean) = runJobs(listOf(url)) { api, _ ->
        api.post("/api/jobs/youtube", youtubePayload(url, start, end, diarize), ::trackCreation).getString("job_id")
    }
    fun startFile(start: String, end: String, diarize: Boolean) {
        val uri = ui.fileUri ?: return
        val name = ui.fileName; val kind = ui.fileKind
        runJobs(listOf(name)) { api, _ ->
            ui = ui.copy(message = "上傳中...", cards = listOf(Card(name, state = "uploading")))
            val body = withContext(Dispatchers.IO) { uploadBody(uri, name, start, end, diarize) }
            api.request("/api/jobs/$kind", body, ::trackCreation).getString("job_id")
        }
    }
    fun startPc(device: Int, lowLatency: Boolean) = runJobs(listOf("電腦播放聲音（PC）")) { api, _ ->
        api.post("/api/jobs/system-audio", audioPayload(device, lowLatency), ::trackCreation).getString("job_id")
    }
    fun stop() {
        if (!ui.busy) return
        stopRequested = true
        ui = ui.copy(stopping = true, message = "停止中")
        if (activeId == null) creationCall?.cancel()
    }
    fun export(uri: Uri, text: String) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    val stream = getApplication<Application>().contentResolver.openOutputStream(uri, "wt") ?: throw IOException()
                    stream.bufferedWriter(Charsets.UTF_8).use { it.write(text) }
                }
                ui = ui.copy(message = "字幕已儲存")
            } catch (e: Exception) { ui = ui.copy(message = "儲存失敗，請重新選擇位置") }
        }
    }
    override fun onCleared() {
        creationCall?.cancel()
        apis.forEach { it.close() }
        super.onCleared()
    }
}
