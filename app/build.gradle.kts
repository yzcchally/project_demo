plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

val huaweiSideBySide = providers.gradleProperty("huaweiSideBySide").orNull == "true"
val phoneVoice = rootProject.layout.projectDirectory.dir(".run/phone-voice")
val phoneVoiceAar = phoneVoice.file("sherpa-onnx-2.0.2.aar")
val phoneVoiceAssets = phoneVoice.dir("assets")
val phoneVoicePython = providers.gradleProperty("phoneVoicePython").orElse(
    providers.systemProperty("os.name").map { if (it.startsWith("Windows")) "python" else "python3" },
)
val preparePhoneVoice = tasks.register<Exec>("preparePhoneVoice") {
    val script = rootProject.layout.projectDirectory.file("tools/prepare_phone_voice.py")
    commandLine(phoneVoicePython.get(), script.asFile.absolutePath)
    inputs.file(script)
    outputs.files(phoneVoiceAar, phoneVoiceAssets.file("phone-voice/model.int8.onnx"),
        phoneVoiceAssets.file("phone-voice/tokens.txt"), phoneVoiceAssets.file("phone-voice/sherpa-onnx-LICENSE.txt"))
}

android {
    namespace = "com.example.demo"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "com.example.demo"
        minSdk = 24
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"
        manifestPlaceholders["launcherLabel"] = "@string/app_name"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        if (huaweiSideBySide) ndk.abiFilters.add("arm64-v8a")
    }

    buildTypes {
        debug {
            if (huaweiSideBySide) {
                applicationIdSuffix = ".huaweitest"
                manifestPlaceholders["launcherLabel"] = "@string/huawei_test_app_name"
            }
        }
        release {
            proguardFiles("proguard-phone-voice.pro")
            optimization {
                enable = true
                packageScope = setOf("androidx.**", "kotlin.**", "kotlinx.**")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
    }
    sourceSets.getByName("main") {
        jniLibs.srcDir(rootProject.file(".run/standalone-build/jniLibs"))
        assets.srcDir(rootProject.file(".run/standalone-build/runtime-assets"))
        assets.srcDir(phoneVoiceAssets)
    }
    androidResources.noCompress += "onnx"
    packaging.jniLibs.useLegacyPackaging = true
}

tasks.named("preBuild") { dependsOn(preparePhoneVoice) }

dependencies {
    implementation(files(phoneVoiceAar).builtBy(preparePhoneVoice))
    implementation(libs.okhttp)
    implementation(libs.zxing.embedded)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    testImplementation(libs.junit)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
