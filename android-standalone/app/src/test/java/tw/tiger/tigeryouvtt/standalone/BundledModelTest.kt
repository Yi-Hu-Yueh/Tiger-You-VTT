package tw.tiger.tigeryouvtt.standalone

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest

class BundledModelTest {
    @get:Rule val directory = TemporaryFolder()
    private val content = mapOf("model.int8.onnx" to "synthetic model".toByteArray(), "tokens.txt" to "synthetic tokens".toByteArray())
    private fun hash(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    private val parts get() = content.map { (name, bytes) -> ModelPart(name, bytes.size.toLong(), hash(bytes)) }
    private fun store() = ModelStore(null, directory.root, parts)
    private fun prepare(store: ModelStore = store()) = store.prepareBundled(openAsset = { content.getValue(it).inputStream() })
    private fun source(name: String) = File("src/main/java/tw/tiger/tigeryouvtt/standalone/$name").readText()

    @Test fun exactManifest() {
        assertEquals(listOf("model.int8.onnx", "tokens.txt"), ModelSpec.parts.map { it.name })
        assertEquals(listOf(239233841L, 315894L), ModelSpec.parts.map { it.bytes })
        assertEquals(239549735L, ModelSpec.size)
        assertEquals("sensevoice-small-int8", ModelSpec.ASSET_DIRECTORY)
        assertTrue(ModelSpec.BASE.endsWith("/2365baeacb507f821a0c8120fcee3d484dba7a07/"))
        assertEquals(listOf("c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51", "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"), ModelSpec.parts.map { it.sha256 })
    }
    @Test fun realBuildAssetsMatchManifest() {
        ModelSpec.parts.forEach { part ->
            val asset = File("src/main/assets/${ModelSpec.ASSET_DIRECTORY}/${part.name}")
            assertTrue(asset.isFile); assertEquals(part.bytes, asset.length())
            val digest = MessageDigest.getInstance("SHA-256")
            asset.inputStream().use { input ->
                val buffer = ByteArray(65536)
                while (true) { val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) }
            }
            assertEquals(part.sha256, digest.digest().joinToString("") { "%02x".format(it) })
        }
    }
    @Test fun absentModelRequiresPreparation() { assertFalse(store().installed()) }
    @Test fun freshPreparationIsValidatedAndReady() { prepare(); store().validate {}; assertTrue(store().installed()) }
    @Test fun validFilesAreNeverRecopiedEvenWithLowSpace() {
        prepare()
        store().prepareBundled(availableBytes = { 0 }, openAsset = { error("must not open assets") })
        store().validate {}
    }
    @Test fun truncatedPreparedFileIsRepaired() {
        prepare(); File(directory.root, "model.int8.onnx").writeBytes(byteArrayOf(1))
        assertThrows(IllegalArgumentException::class.java) { store().validate {} }
        prepare(); store().validate {}
    }
    @Test fun sameSizeCorruptPreparedFileIsRepaired() {
        prepare(); File(directory.root, "model.int8.onnx").writeBytes(ByteArray(content.getValue("model.int8.onnx").size))
        assertThrows(IllegalArgumentException::class.java) { store().validate {} }
        prepare(); store().validate {}
    }
    @Test fun corruptBundledAssetNeverBecomesReady() {
        val error = assertThrows(ModelPreparationFailure::class.java) {
            store().prepareBundled(openAsset = { ByteArray(content.getValue(it).size).inputStream() })
        }
        assertTrue(error.userMessage.contains("校驗不符")); assertFalse(store().installed())
        assertFalse(File(directory.root, "model.int8.onnx.part").exists())
    }
    @Test fun truncatedBundledAssetRejected() {
        assertThrows(ModelPreparationFailure::class.java) { store().prepareBundled(openAsset = { byteArrayOf(1).inputStream() }) }
        assertFalse(store().installed())
    }
    @Test fun stalePartialRemovedBeforeRetry() {
        File(directory.root, "model.int8.onnx.part").writeText("interrupted")
        prepare(); store().validate {}; assertFalse(File(directory.root, "model.int8.onnx.part").exists())
    }
    @Test fun cancellationDuringCopyCleansPartialAndAllowsRetry() {
        assertThrows(StopRequested::class.java) {
            store().prepareBundled(progress = { _, _, _ -> throw StopRequested() }, openAsset = { content.getValue(it).inputStream() })
        }
        assertFalse(store().installed()); assertFalse(File(directory.root, "model.int8.onnx.part").exists())
        prepare(); store().validate {}
    }
    @Test fun interruptionBetweenFilesPreservesValidFirstFile() {
        assertThrows(IOException::class.java) {
            store().prepareBundled(openAsset = { if (it == "tokens.txt") throw IOException("private/path") else content.getValue(it).inputStream() })
        }
        assertFalse(store().installed())
        val opened = mutableListOf<String>()
        store().prepareBundled(openAsset = { opened += it; content.getValue(it).inputStream() })
        assertEquals(listOf("tokens.txt"), opened); store().validate {}
    }
    @Test fun insufficientSpaceFailsBeforeAssetOpenAndRemovesStaleTemp() {
        File(directory.root, "tokens.txt.part").writeText("stale")
        val failure = assertThrows(ModelPreparationFailure::class.java) {
            store().prepareBundled(availableBytes = { 0 }, openAsset = { error("must not copy") })
        }
        assertTrue(failure.userMessage.contains("儲存空間不足")); assertFalse(store().installed())
        assertFalse(File(directory.root, "tokens.txt.part").exists())
    }
    @Test fun exactStorageThresholdAccepted() {
        store().prepareBundled(availableBytes = { parts.sumOf { it.bytes } + ModelSpec.STORAGE_RESERVE }, openAsset = { content.getValue(it).inputStream() })
        store().validate {}
    }
    @Test fun oneByteBelowStorageThresholdRejected() {
        assertThrows(ModelPreparationFailure::class.java) {
            store().prepareBundled(availableBytes = { parts.sumOf { it.bytes } + ModelSpec.STORAGE_RESERVE - 1 }, openAsset = { error("must not copy") })
        }
    }
    @Test fun midCopyIoFailureIsSanitizedAndCleaned() {
        val failure = assertThrows(ModelPreparationFailure::class.java) {
            store().prepareBundled(openAsset = { object : InputStream() {
                override fun read(): Int = throw IOException("/secret/native/path ENOSPC")
            } })
        }
        assertFalse(failure.message!!.contains("secret")); assertFalse(store().installed())
        assertFalse(File(directory.root, "model.int8.onnx.part").exists())
    }
    @Test fun preparationPreservesUnrelatedFiles() {
        val other = File(directory.root, "owner.txt").apply { writeText("keep") }
        prepare(); assertEquals("keep", other.readText())
    }
    @Test fun localOnlyPreparationContract() {
        val storeSource = source("ModelStore.kt")
        assertTrue(storeSource.contains("assets.open("))
        listOf("java.net", "HttpURLConnection", "openConnection", "fun download", "fun importFile").forEach { assertFalse(storeSource.contains(it)) }
        assertOfflineCoreHasNoNetworkDependencies()
        assertFalse(source("LocalService.kt").contains("models.download"))
    }
    @Test fun privateAtomicPreparationContract() {
        val code = source("ModelStore.kt")
        assertTrue(code.contains(".filesDir")); assertTrue(code.contains("StandardCopyOption.ATOMIC_MOVE"))
        assertTrue(code.contains("output.fd.sync()"))
    }
    @Test fun readyAndMediaRequiredForStart() {
        assertTrue(canStartTranscription(true, false, true))
        assertFalse(canStartTranscription(false, false, true))
        assertFalse(canStartTranscription(true, true, true))
        assertFalse(canStartTranscription(true, false, false))
    }
    @Test fun automaticPreparationAndUiWiring() {
        val ui = source("MainActivity.kt")
        assertTrue(ui.contains("setAction(\"prepare\")"))
        assertTrue(ui.contains("canStartTranscription(state.modelReady, state.busy, uriText.isNotEmpty())"))
        assertTrue(ui.contains("來源：APK 內建"))
        assertFalse(ui.contains("begin(\"download\")")); assertFalse(ui.contains("begin(\"import\""))
        val service = source("LocalService.kt")
        assertTrue(service.indexOf("store.validate(checkWork)") < service.indexOf("val recognizer = OfflineRecognizer"))
        assertFalse(service.contains("e.message"))
    }
}
