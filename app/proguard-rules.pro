# osmdroid nutzt Reflection fuer seine Tile-Sources und Preferences.
-keep class org.osmdroid.** { *; }
-dontwarn org.osmdroid.**

# OkHttp / Okio
-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
