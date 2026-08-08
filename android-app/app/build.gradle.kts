import java.text.SimpleDateFormat
import java.util.Date

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.radiozapper.mvp"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.radiozapper.mvp"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"

        // Build-Zeitstempel statt manuell gepflegter versionName/versionCode:
        // bei der Update-Frequenz dieses Prototyps (mehrere Builds pro Tag)
        // waere das Hochzaehlen selbst schnell veraltet/vergessen. Der
        // Zeitstempel entsteht automatisch bei jedem Build und macht auf
        // einen Blick sichtbar, welcher Stand auf dem Handy installiert ist
        // (siehe Anlass: Auto-Switch schien "nicht zu funktionieren" - war
        // vermutlich nur eine veraltete APK).
        buildConfigField(
            "String",
            "BUILD_TIME",
            "\"${SimpleDateFormat("yyyy-MM-dd HH:mm").format(Date())}\""
        )
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.lifecycle:lifecycle-service:2.8.4")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    implementation("androidx.media3:media3-exoplayer:1.4.1")
    implementation("androidx.media3:media3-common:1.4.1")

    implementation("com.alphacephei:vosk-android:0.3.47")

    // Fuer den News-Break-Ordner (SAF-Tree-Uri, siehe newsbreak/NewsBreakSettings.kt) -
    // AndroidX-Standardweg, um Kind-Dateien einer persistierten Tree-Uri aufzulisten,
    // ohne DocumentsContract-Cursor-Handling von Hand.
    implementation("androidx.documentfile:documentfile:1.0.1")
}
