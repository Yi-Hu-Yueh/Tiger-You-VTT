import java.security.MessageDigest

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}
android {
    namespace = "tw.tiger.tigeryouvtt.standalone"
    compileSdk { version = release(36) { minorApiLevel = 1 } }
    buildToolsVersion = "36.0.0"
    defaultConfig {
        applicationId = "tw.tiger.tigeryouvtt.standalone"
        minSdk = 29
        targetSdk = 36
        versionCode = 7
        versionName = "0.4.1-checkpoint-d2r"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }
    buildFeatures { compose = true }
    androidResources { noCompress += listOf("onnx", "txt") }
    compileOptions {
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
val nativeRuntime = rootProject.file("../.runtime/standalone-deps/sherpa-onnx-1.13.8.aar")
tasks.register("verifyNativeRuntime") {
    doLast {
        check(nativeRuntime.isFile) { "Run tools/fetch-runtime.ps1 first" }
        val digest = MessageDigest.getInstance("SHA-256")
        nativeRuntime.inputStream().use { input ->
            val buffer = ByteArray(65536)
            while (true) { val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) }
        }
        check(digest.digest().joinToString("") { "%02x".format(it) } == "633c24321e06b1fe79feafa03ea16cbc0f8a286641e2da3559bac91bdb13bd96") { "Native runtime SHA256 mismatch" }
    }
}
tasks.named("preBuild") { dependsOn("verifyNativeRuntime") }
tasks.register("verifyBundledModel") {
    doLast {
        val expected = listOf(
            Triple("model.int8.onnx", 239233841L, "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"),
            Triple("tokens.txt", 315894L, "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc")
        )
        expected.forEach { (name, size, hash) ->
            val asset = file("src/main/assets/sensevoice-small-int8/$name")
            check(asset.isFile && asset.length() == size) { "Bundled model missing or wrong size: $name; see README" }
            val digest = MessageDigest.getInstance("SHA-256")
            asset.inputStream().use { input ->
                val buffer = ByteArray(65536)
                while (true) { val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) }
            }
            check(digest.digest().joinToString("") { "%02x".format(it) } == hash) { "Bundled model SHA256 mismatch: $name" }
        }
    }
}
tasks.named("preBuild") { dependsOn("verifyBundledModel") }
dependencies {
    implementation("com.github.teamnewpipe:NewPipeExtractor:v0.26.5")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jsoup:jsoup:1.22.2")
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs_nio:2.1.5")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    implementation(files(nativeRuntime))
    implementation(platform("androidx.compose:compose-bom:2026.02.01"))
    implementation("androidx.activity:activity-compose:1.8.2")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.ui:ui")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    testImplementation("junit:junit:4.13.2")
}

// Explicit opt-in network gate; normal unit tests never run this main class.
tasks.register<JavaExec>("youtubeSmoke") {
    dependsOn("testDebugUnitTest")
    mainClass.set("tw.tiger.tigeryouvtt.standalone.YouTubeSmoke")
    args("--live")
    doFirst { classpath = tasks.named<Test>("testDebugUnitTest").get().classpath }
}
