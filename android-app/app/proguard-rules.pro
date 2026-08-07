# Debug-Build hat isMinifyEnabled=false; diese Regeln greifen erst bei einem Release-Build.
-keep class org.vosk.** { *; }
-keep class com.sun.jna.** { *; }
-dontwarn com.sun.jna.**
