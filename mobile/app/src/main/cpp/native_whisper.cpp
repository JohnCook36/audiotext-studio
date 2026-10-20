#include <jni.h>
#include <string>
#include <vector>
#include <thread>
#include "whisper.h"

extern "C" JNIEXPORT jlong JNICALL
Java_ru_audiotext_studio_NativeWhisper_open(JNIEnv *env, jobject, jstring path) {
    const char *chars = env->GetStringUTFChars(path, nullptr);
    struct whisper_context_params p = whisper_context_default_params();
    p.use_gpu = false;
    whisper_context *ctx = whisper_init_from_file_with_params(chars, p);
    env->ReleaseStringUTFChars(path, chars);
    return reinterpret_cast<jlong>(ctx);
}

extern "C" JNIEXPORT void JNICALL
Java_ru_audiotext_studio_NativeWhisper_close(JNIEnv *, jobject, jlong handle) {
    if (handle) whisper_free(reinterpret_cast<whisper_context *>(handle));
}

extern "C" JNIEXPORT jobjectArray JNICALL
Java_ru_audiotext_studio_NativeWhisper_recognize(JNIEnv *env, jobject, jlong handle, jfloatArray sound) {
    jclass cls = env->FindClass("java/lang/String");
    if (!handle || !sound) return env->NewObjectArray(0, cls, nullptr);
    whisper_context *ctx = reinterpret_cast<whisper_context *>(handle);
    jsize length = env->GetArrayLength(sound);
    if (length <= 0) return env->NewObjectArray(0, cls, nullptr);
    std::vector<float> pcm(static_cast<size_t>(length));
    env->GetFloatArrayRegion(sound, 0, length, pcm.data());
    whisper_full_params p = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
    p.print_realtime = false;
    p.print_progress = false;
    p.print_timestamps = false;
    p.print_special = false;
    p.no_context = true;
    p.no_timestamps = false;
    p.translate = false;
    p.language = "auto";
    p.n_threads = 4;
    p.offset_ms = 0;
    int result = whisper_full(ctx, p, pcm.data(), length);
    if (result != 0) {
        jclass ex = env->FindClass("java/lang/IllegalStateException");
        env->ThrowNew(ex, "whisper_full failed");
        return nullptr;
    }
    int n = whisper_full_n_segments(ctx);
    jobjectArray output = env->NewObjectArray(n, cls, nullptr);
    for (int i = 0; i < n; ++i) {
        // times in hundredths of a second; Kotlin parses milliseconds
        int64_t start = whisper_full_get_segment_t0(ctx, i) * 10;
        int64_t end = whisper_full_get_segment_t1(ctx, i) * 10;
        const char *text = whisper_full_get_segment_text(ctx, i);
        std::string record = std::to_string(start) + "\t" + std::to_string(end) + "\t" + (text ? text : "");
        jstring item = env->NewStringUTF(record.c_str());
        env->SetObjectArrayElement(output, i, item);
        env->DeleteLocalRef(item);
    }
    return output;
}
