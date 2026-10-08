package tw.tiger.tigeryouvtt.standalone

import android.content.Context
import java.io.File
import java.io.InputStream
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

data class ModelPart(val name: String, val bytes: Long, val sha256: String)
object ModelSpec {
    const val NAME = "SenseVoice Small INT8 · 中/英/粵/日/韓"
    const val ASSET_DIRECTORY = "sensevoice-small-int8"
    const val STORAGE_RESERVE = 64L * 1024 * 1024
    const val BASE = "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/2365baeacb507f821a0c8120fcee3d484dba7a07/"
    val parts = listOf(
        ModelPart("model.int8.onnx", 239233841, "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"),
        ModelPart("tokens.txt", 315894, "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc")
    )
    val size = parts.sumOf { it.bytes }
}
class ModelPreparationFailure(val userMessage: String) : IOException(userMessage)

fun canStartTranscription(ready: Boolean, busy: Boolean, mediaSelected: Boolean) = ready && !busy && mediaSelected

class ModelStore(private val context: Context?, val root: File = File(requireNotNull(context).filesDir, ModelSpec.ASSET_DIRECTORY), private val parts: List<ModelPart> = ModelSpec.parts) {
    init { root.mkdirs() }
    fun installed() = parts.all { part -> confinedFile(root, part.name).let { it.isFile && it.length() == part.bytes } }
    fun validate(check: () -> Unit) {
        require(installed()) { "內建模型尚未準備完成，請重新準備模型" }
        parts.forEach { part ->
            require(valid(part, check)) { "內建模型校驗失敗，請重新準備模型" }
        }
    }
    private fun valid(part: ModelPart, check: () -> Unit): Boolean {
        val file = confinedFile(root, part.name)
        if (!file.isFile || file.length() != part.bytes) return false
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(65536)
            while (true) { check(); val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) }
        }
        return hex(digest.digest()) == part.sha256
    }
    private fun partialFile(part: ModelPart): File = File(root, part.name + ".part").also {
        require(it.canonicalFile.parentFile == root.canonicalFile) { "模型暫存路徑無效" }
    }

    /** Only AssetManager is used in production; injected streams permit offline JVM tests. */
    fun prepareBundled(
        check: () -> Unit = {},
        progress: (String, Long, Long) -> Unit = { _, _, _ -> },
        availableBytes: () -> Long = { root.usableSpace },
        openAsset: (String) -> InputStream = { name -> requireNotNull(context).assets.open("${ModelSpec.ASSET_DIRECTORY}/$name") }
    ) {
        try {
            check()
            if (!root.isDirectory && !root.mkdirs()) throw IOException()
            // Only this store's known partial files are removed. All calls run under the job gate.
            parts.forEach { part ->
                val partial = partialFile(part)
                if (partial.exists() && !partial.delete()) throw IOException()
            }
            val missing = parts.filterNot { valid(it, check) }
            if (missing.isEmpty()) return // Hash-verified: no asset open or redundant copy.
            val required = missing.sumOf { it.bytes } + ModelSpec.STORAGE_RESERVE
            if (availableBytes() < required) throw ModelPreparationFailure(
                "內部儲存空間不足，請至少釋出 ${(required + 1048575) / 1048576} MiB 後重試；模型尚未就緒"
            )
            missing.forEach { part ->
                check()
                openAsset(part.name).use { input -> install(part, input, check) { done, total -> progress(part.name, done, total) } }
            }
            validate(check)
        } catch (e: StopRequested) { throw e
        } catch (e: ModelPreparationFailure) { throw e
        } catch (_: IllegalArgumentException) {
            throw ModelPreparationFailure("內建模型大小或校驗不符，請重新安裝完整 APK；模型尚未就緒")
        } catch (_: IOException) {
            throw ModelPreparationFailure("內建模型準備失敗，請確認內部儲存空間後重試；模型尚未就緒")
        } catch (_: SecurityException) {
            throw ModelPreparationFailure("無法存取 App 內部模型，請重新啟動或安裝完整 APK")
        }
    }
    internal fun install(part: ModelPart, input: InputStream, check: () -> Unit, progress: (Long, Long) -> Unit) {
        val target = confinedFile(root, part.name)
        val partial = partialFile(part)
        val digest = MessageDigest.getInstance("SHA-256")
        var copied = 0L
        try {
            partial.outputStream().use { output ->
                val buffer = ByteArray(65536)
                while (true) {
                    check()
                    val n = input.read(buffer)
                    if (n < 0) break
                    copied += n
                    require(copied <= part.bytes) { "模型大小不符" }
                    output.write(buffer, 0, n); digest.update(buffer, 0, n)
                    progress(copied, part.bytes)
                }
                output.fd.sync()
            }
            require(copied == part.bytes && hex(digest.digest()) == part.sha256) { "模型大小或 SHA256 不符，未覆蓋既有模型" }
            check()
            Files.move(partial.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally { partial.delete() }
    }
    fun delete() { parts.forEach { confinedFile(root, it.name).let { file -> check(!file.exists() || file.delete()) } } }
    private fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) }
}
