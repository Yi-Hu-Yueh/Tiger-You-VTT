plugins { application }
repositories {
    mavenCentral()
    maven("https://jitpack.io") {
        content { includeGroup("com.github.teamnewpipe"); includeGroup("com.github.TeamNewPipe") }
    }
}
dependencies { implementation("com.github.teamnewpipe:NewPipeExtractor:v0.26.5") }
java { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
application { mainClass.set("YoutubeSmoke") }
