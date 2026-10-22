#include <android/log.h>
#include <jni.h>

#include <string>
#include <vector>

#include "vr_player.h"

#define JNI_METHOD(return_type, method_name) \
  JNIEXPORT return_type JNICALL Java_com_google_cardboard_VrActivity_##method_name

namespace {

inline jlong jptr(genova::VrPlayer* p) { return reinterpret_cast<intptr_t>(p); }
inline genova::VrPlayer* native(jlong ptr) {
  return reinterpret_cast<genova::VrPlayer*>(ptr);
}

JavaVM* javaVm;

std::string ToStd(JNIEnv* env, jstring s) {
  if (s == nullptr) return "";
  const char* c = env->GetStringUTFChars(s, nullptr);
  std::string r(c);
  env->ReleaseStringUTFChars(s, c);
  return r;
}

}  // namespace

extern "C" {

JNIEXPORT jint JNI_OnLoad(JavaVM* vm, void*) {
  javaVm = vm;
  return JNI_VERSION_1_6;
}

JNI_METHOD(jlong, nativeOnCreate)(JNIEnv*, jobject obj) {
  return jptr(new genova::VrPlayer(javaVm, obj));
}

JNI_METHOD(void, nativeOnDestroy)(JNIEnv*, jobject, jlong p) { delete native(p); }

JNI_METHOD(void, nativeOnSurfaceCreated)(JNIEnv* env, jobject, jlong p) {
  native(p)->OnSurfaceCreated(env);
}

JNI_METHOD(void, nativeOnDrawFrame)(JNIEnv* env, jobject, jlong p) {
  native(p)->OnDrawFrame(env);
}

JNI_METHOD(void, nativeOnTriggerEvent)(JNIEnv*, jobject, jlong p) {
  native(p)->OnTriggerEvent();
}

JNI_METHOD(void, nativeOnRecenter)(JNIEnv*, jobject, jlong p) { native(p)->Recenter(); }

JNI_METHOD(void, nativeOnPause)(JNIEnv*, jobject, jlong p) { native(p)->OnPause(); }
JNI_METHOD(void, nativeOnResume)(JNIEnv*, jobject, jlong p) { native(p)->OnResume(); }

JNI_METHOD(void, nativeSetScreenParams)(JNIEnv*, jobject, jlong p, jint w, jint h) {
  native(p)->SetScreenParams(w, h);
}

JNI_METHOD(void, nativeSwitchViewer)(JNIEnv*, jobject, jlong p) {
  native(p)->SwitchViewer();
}

JNI_METHOD(void, nativeSetScene)
(JNIEnv* env, jobject, jlong p, jfloatArray data, jobjectArray keys) {
  const jsize n = env->GetArrayLength(data);
  std::vector<float> v(n);
  env->GetFloatArrayRegion(data, 0, n, v.data());
  std::vector<std::string> k;
  const jsize kn = keys ? env->GetArrayLength(keys) : 0;
  for (jsize i = 0; i < kn; ++i) {
    jstring s = static_cast<jstring>(env->GetObjectArrayElement(keys, i));
    k.push_back(ToStd(env, s));
    env->DeleteLocalRef(s);
  }
  native(p)->SetScene(v, k);
}

JNI_METHOD(void, nativeMoveCamera)
(JNIEnv*, jobject, jlong p, jfloat x, jfloat y, jfloat z, jint focus, jfloat sec,
 jint follow) {
  native(p)->MoveCamera(x, y, z, focus, sec, follow);
}

JNI_METHOD(void, nativeSetAnimations)(JNIEnv* env, jobject, jlong p, jfloatArray data) {
  const jsize n = data ? env->GetArrayLength(data) : 0;
  std::vector<float> v(n);
  if (n) env->GetFloatArrayRegion(data, 0, n, v.data());
  native(p)->SetAnimations(v);
}

JNI_METHOD(void, nativeTweenNode)
(JNIEnv*, jobject, jlong p, jint node, jint prop, jfloat x, jfloat y, jfloat z, jfloat seconds) {
  native(p)->TweenNode(node, prop, x, y, z, seconds);
}

JNI_METHOD(void, nativeSetFog)(JNIEnv* env, jobject, jlong player, jfloatArray data) {
  const jsize count = data ? env->GetArrayLength(data) : 0;
  std::vector<float> values(count);
  if (count) env->GetFloatArrayRegion(data, 0, count, values.data());
  native(player)->SetFog(values);
}

JNI_METHOD(void, nativeBurst)(JNIEnv*, jobject, jlong player, jint node) {
  native(player)->Burst(node);
}

JNI_METHOD(void, nativeSetHighlight)(JNIEnv*, jobject, jlong p, jint node) {
  native(p)->SetHighlight(node);
}

JNI_METHOD(void, nativeSetSubtitle)(JNIEnv* env, jobject, jlong p, jstring s) {
  native(p)->SetSubtitle(ToStd(env, s));
}

JNI_METHOD(void, nativeSetFixedPose)(JNIEnv*, jobject, jlong p, jboolean on, jfloat seconds) {
  native(p)->SetFixedPose(on, seconds);
}

JNI_METHOD(void, nativeSetGazeEnabled)(JNIEnv*, jobject, jlong p, jboolean on) {
  native(p)->SetGazeEnabled(on);
}

}  // extern "C"
