#include "vr_player.h"

#include <android/log.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>

#include "cardboard.h"

#define TAG "GenovaPlayer"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define LOGE2(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

namespace genova {
using ndk_hello_cardboard::GetBootTimeNano;
using ndk_hello_cardboard::LoadGLShader;
using ndk_hello_cardboard::CheckGlError;
namespace {

constexpr float kPi = 3.14159265358979f;
constexpr float kZNear = 0.05f, kZFar = 400.f;
constexpr int kVelocityFilterCutoffFrequency = 6;
constexpr uint64_t kPredictionTimeWithoutVsyncNanos = 50000000;
constexpr float kGazeDwellSeconds = 1.6f;
constexpr float kMinPickAngle = 0.13f;

constexpr const char* kVertexShader = R"glsl(
uniform mat4 u_Model;
uniform mat4 u_VP;
uniform mediump float u_DynamicCount;
uniform vec4 u_LightPositions[4];
uniform vec4 u_LightColors[4];
uniform vec4 u_LightDirections[4];
uniform vec2 u_LightCones[4];
attribute vec3 a_Pos;
attribute vec3 a_Nrm;
attribute vec2 a_UV;
attribute vec4 a_Col;
varying mediump vec2 v_UV;
varying mediump vec4 v_Col;
varying mediump vec3 v_N;
varying mediump vec3 v_World;
varying mediump vec3 v_Local;
varying mediump vec3 v_Lighting;
void main() {
  vec4 w = u_Model * vec4(a_Pos, 1.0);
  v_World = w.xyz;
  v_N = mat3(u_Model) * a_Nrm;
  v_UV = a_UV;
  v_Col = a_Col;
  v_Local = a_Pos;
  vec3 normal = normalize(v_N);
  vec3 illumination = vec3(0.12);
  if (u_DynamicCount > 0.5) {
    for (int index = 0; index < 4; index++) {
      vec3 offset = u_LightPositions[index].xyz - w.xyz;
      float distance = max(length(offset), 0.001);
      vec3 direction = offset / distance;
      float attenuation = max(0.0, 1.0 - distance / max(u_LightPositions[index].w, 0.001));
      float cone = 1.0;
      if (u_LightDirections[index].w > 0.5) {
        float cosine = dot(-direction, u_LightDirections[index].xyz);
        cone = smoothstep(u_LightCones[index].x, u_LightCones[index].y, cosine);
      }
      illumination += u_LightColors[index].rgb * u_LightColors[index].w
          * max(dot(normal, direction), 0.0) * attenuation * attenuation * cone;
    }
  }
  v_Lighting = illumination;
  gl_Position = u_VP * w;
})glsl";

constexpr const char* kFragmentShader = R"glsl(
precision mediump float;
uniform sampler2D u_Tex;
uniform vec4 u_Color;
uniform float u_Lit;
uniform vec3 u_Light;
uniform mediump float u_DynamicCount;
uniform float u_UseTex;
uniform float u_Inner;
uniform float u_Sky;
uniform vec4 u_FogColorDensity;
uniform vec3 u_FogShape;
uniform vec3 u_FogEye;
uniform float u_FogEnabled;
varying vec2 v_UV;
varying vec4 v_Col;
varying vec3 v_N;
varying vec3 v_World;
varying vec3 v_Local;
varying vec3 v_Lighting;
void main() {
  if (u_Inner > 0.0 && length(v_Local.xy) < u_Inner) discard;
  vec4 base = u_Color * v_Col;
  if (u_UseTex > 0.5) base *= texture2D(u_Tex, v_UV);
  if (u_Lit > 0.5) {
    if (u_DynamicCount < 0.5) {
      vec3 normal = normalize(v_N);
      vec3 direction = normalize(u_Light - v_World);
      base.rgb *= 0.12 + 0.88 * max(dot(normal, direction), 0.0);
    } else {
      base.rgb *= v_Lighting;
    }
  }
  float height_density = exp(clamp(-(v_World.y - u_FogShape.x) * u_FogShape.y, -10.0, 10.0));
  float fog = u_FogEnabled * (1.0 - exp(-u_FogColorDensity.w * height_density
      * max(0.0, length(v_World - u_FogEye) - u_FogShape.z)));
  base.rgb = mix(base.rgb, u_FogColorDensity.rgb, fog);
  gl_FragColor = base;
})glsl";

constexpr const char* kParticleVertexShader = R"glsl(
uniform mat4 u_VP;
uniform float u_ProjectionScale;
uniform float u_MaxPointSize;
attribute vec3 a_Position;
attribute vec4 a_Color;
attribute float a_Size;
attribute float a_Style;
varying mediump vec4 v_Color;
varying mediump vec3 v_World;
varying mediump float v_Style;
void main() {
  vec4 clip = u_VP * vec4(a_Position, 1.0);
  gl_Position = clip;
  gl_PointSize = clamp(a_Size * u_ProjectionScale / max(clip.w, 0.01), 1.0, u_MaxPointSize);
  v_Color = a_Color;
  v_World = a_Position;
  v_Style = a_Style;
})glsl";

constexpr const char* kParticleFragmentShader = R"glsl(
precision mediump float;
uniform vec4 u_FogColorDensity;
uniform vec3 u_FogShape;
uniform vec3 u_FogEye;
uniform float u_FogEnabled;
uniform float u_Additive;
varying vec4 v_Color;
varying vec3 v_World;
varying float v_Style;
void main() {
  vec2 point = gl_PointCoord * 2.0 - 1.0;
  float radius = length(point);
  float alpha = 1.0 - smoothstep(0.0, 1.0, radius);
  if (v_Style > 0.5 && v_Style < 1.5) {
    alpha = (1.0 - smoothstep(0.0, 0.35, abs(point.x))) * (1.0 - smoothstep(0.2, 1.0, abs(point.y)));
  } else if (v_Style > 1.5 && v_Style < 2.5) {
    alpha *= 0.75 + 0.25 * sin(point.x * 11.0) * sin(point.y * 9.0);
  } else if (v_Style > 2.5 && v_Style < 3.5) {
    alpha = (1.0 - smoothstep(0.08, 0.22, abs(radius - 0.72))) * step(radius, 1.0);
  } else if (v_Style > 3.5) {
    alpha = max(1.0 - smoothstep(0.02, 0.13, abs(point.x)),
                1.0 - smoothstep(0.02, 0.13, abs(point.y))) * (1.0 - smoothstep(0.0, 1.0, radius));
  }
  float height_density = exp(clamp(-(v_World.y - u_FogShape.x) * u_FogShape.y, -10.0, 10.0));
  float fog = u_FogEnabled * (1.0 - exp(-u_FogColorDensity.w * height_density
      * max(0.0, length(v_World - u_FogEye) - u_FogShape.z)));
  vec3 color = mix(v_Color.rgb, u_FogColorDensity.rgb, fog * (1.0 - u_Additive));
  gl_FragColor = vec4(color, v_Color.a * alpha * mix(1.0, 1.0 - fog, u_Additive));
})glsl";

// ---- tiny column-major mat4 helpers -------------------------------------
using M4 = std::array<float, 16>;

M4 Identity() {
  M4 m{};
  m[0] = m[5] = m[10] = m[15] = 1;
  return m;
}

M4 Mul(const M4& a, const M4& b) {
  M4 r{};
  for (int c = 0; c < 4; ++c)
    for (int row = 0; row < 4; ++row) {
      float s = 0;
      for (int k = 0; k < 4; ++k) s += a[k * 4 + row] * b[c * 4 + k];
      r[c * 4 + row] = s;
    }
  return r;
}

M4 Translate(float x, float y, float z) {
  M4 m = Identity();
  m[12] = x;
  m[13] = y;
  m[14] = z;
  return m;
}

M4 Scale(float x, float y, float z) {
  M4 m = Identity();
  m[0] = x;
  m[5] = y;
  m[10] = z;
  return m;
}

M4 RotY(float a) {
  M4 m = Identity();
  m[0] = std::cos(a);
  m[8] = std::sin(a);
  m[2] = -std::sin(a);
  m[10] = std::cos(a);
  return m;
}

M4 RotX(float a) {
  M4 m = Identity();
  m[5] = std::cos(a);
  m[9] = -std::sin(a);
  m[6] = std::sin(a);
  m[10] = std::cos(a);
  return m;
}

M4 RotZ(float a) {
  M4 m = Identity();
  m[0] = std::cos(a);
  m[4] = -std::sin(a);
  m[1] = std::sin(a);
  m[5] = std::cos(a);
  return m;
}

M4 FromQuat(const float q[4]) {
  const float x = q[0], y = q[1], z = q[2], w = q[3];
  M4 m = Identity();
  m[0] = 1 - 2 * (y * y + z * z);
  m[1] = 2 * (x * y + z * w);
  m[2] = 2 * (x * z - y * w);
  m[4] = 2 * (x * y - z * w);
  m[5] = 1 - 2 * (x * x + z * z);
  m[6] = 2 * (y * z + x * w);
  m[8] = 2 * (x * z + y * w);
  m[9] = 2 * (y * z - x * w);
  m[10] = 1 - 2 * (x * x + y * y);
  return m;
}

M4 FromGl(const float* a) {
  M4 m;
  std::memcpy(m.data(), a, sizeof(float) * 16);
  return m;
}

// Rotates vector v by the inverse of the rotation in m (m is a pure rotation).
void RotateInverse(const M4& m, const float v[3], float out[3]) {
  out[0] = m[0] * v[0] + m[1] * v[1] + m[2] * v[2];
  out[1] = m[4] * v[0] + m[5] * v[1] + m[6] * v[2];
  out[2] = m[8] * v[0] + m[9] * v[1] + m[10] * v[2];
}

float Smooth(float t) {
  t = std::min(1.f, std::max(0.f, t));
  return t * t * (3 - 2 * t);
}

double NowSeconds() { return GetBootTimeNano() / 1e9; }

}  // namespace

VrPlayer::VrPlayer(JavaVM* vm, jobject activity) : vm_(vm) {
  JNIEnv* env;
  vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6);
  activity_ = env->NewGlobalRef(activity);
  jclass cls = env->GetObjectClass(activity);
  load_texture_mid_ = env->GetMethodID(cls, "loadTexture", "(Ljava/lang/String;)[I");
  on_gaze_mid_ = env->GetMethodID(cls, "onGaze", "(I)V");
  look_down_mid_ = env->GetMethodID(cls, "onLookDown", "(FFFFF)V");
  model_path_mid_ = env->GetMethodID(cls, "modelPath", "(Ljava/lang/String;)Ljava/lang/String;");

  Cardboard_initializeAndroid(vm, activity_);
  head_tracker_ = CardboardHeadTracker_create();
  CardboardHeadTracker_setLowPassFilter(head_tracker_,
                                        kVelocityFilterCutoffFrequency);
  t0_ = NowSeconds();
}

VrPlayer::~VrPlayer() {
  CardboardHeadTracker_destroy(head_tracker_);
  CardboardLensDistortion_destroy(lens_distortion_);
  CardboardDistortionRenderer_destroy(distortion_renderer_);
  JNIEnv* env;
  vm_->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6);
  env->DeleteGlobalRef(activity_);
}

// ---- meshes -------------------------------------------------------------
namespace {

Mesh Upload(const std::vector<float>& v, const std::vector<unsigned short>& idx) {
  Mesh m;
  glGenBuffers(1, &m.vbo);
  glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
  glBufferData(GL_ARRAY_BUFFER, v.size() * sizeof(float), v.data(),
               GL_STATIC_DRAW);
  glGenBuffers(1, &m.ibo);
  glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, m.ibo);
  glBufferData(GL_ELEMENT_ARRAY_BUFFER, idx.size() * sizeof(unsigned short),
               idx.data(), GL_STATIC_DRAW);
  m.index_count = static_cast<int>(idx.size());
  return m;
}

// vertex layout: pos(3) normal(3) uv(2)
Mesh MakeSphere(int lat, int lon) {
  std::vector<float> v;
  std::vector<unsigned short> idx;
  for (int i = 0; i <= lat; ++i) {
    const float th = kPi * i / lat;
    for (int j = 0; j <= lon; ++j) {
      const float ph = 2 * kPi * j / lon;
      const float x = std::sin(th) * std::cos(ph);
      const float y = std::cos(th);
      const float z = std::sin(th) * std::sin(ph);
      v.insert(v.end(), {x, y, z, x, y, z, 1.f - static_cast<float>(j) / lon,
                         static_cast<float>(i) / lat});
    }
  }
  for (int i = 0; i < lat; ++i)
    for (int j = 0; j < lon; ++j) {
      const unsigned short a = i * (lon + 1) + j, b = a + lon + 1;
      idx.insert(idx.end(), {a, b, static_cast<unsigned short>(a + 1),
                             static_cast<unsigned short>(a + 1), b,
                             static_cast<unsigned short>(b + 1)});
    }
  return Upload(v, idx);
}

// Flat disc in the XY plane (radius 1), uv.x = radial coordinate. The shader
// discards fragments below u_Inner, which turns it into a ring.
Mesh MakeDisc(int segments) {
  std::vector<float> v;
  std::vector<unsigned short> idx;
  const int rings = 2;
  for (int r = 0; r <= rings; ++r) {
    const float rad = static_cast<float>(r) / rings;
    for (int s = 0; s <= segments; ++s) {
      const float a = 2 * kPi * s / segments;
      v.insert(v.end(), {rad * std::cos(a), rad * std::sin(a), 0, 0, 0, 1, rad,
                         static_cast<float>(s) / segments});
    }
  }
  for (int r = 0; r < rings; ++r)
    for (int s = 0; s < segments; ++s) {
      const unsigned short a = r * (segments + 1) + s, b = a + segments + 1;
      idx.insert(idx.end(), {a, b, static_cast<unsigned short>(a + 1),
                             static_cast<unsigned short>(a + 1), b,
                             static_cast<unsigned short>(b + 1)});
    }
  return Upload(v, idx);
}

Mesh MakeQuad() {
  std::vector<float> v = {-1, -1, 0, 0, 0, 1, 0, 1, 1, -1, 0, 0, 0, 1, 1, 1,
                          1,  1,  0, 0, 0, 1, 1, 0, -1, 1, 0, 0, 0, 1, 0, 0};
  std::vector<unsigned short> idx = {0, 1, 2, 0, 2, 3};
  return Upload(v, idx);
}


Mesh MakeBox() {
  // 6 faces, each with its own normals and 0..1 uv. Half-extent 1.
  struct F { float n[3], u[3], v[3]; };
  const F faces[6] = {{{0, 0, 1}, {1, 0, 0}, {0, 1, 0}},   {{0, 0, -1}, {-1, 0, 0}, {0, 1, 0}},
                      {{1, 0, 0}, {0, 0, -1}, {0, 1, 0}},  {{-1, 0, 0}, {0, 0, 1}, {0, 1, 0}},
                      {{0, 1, 0}, {1, 0, 0}, {0, 0, -1}},  {{0, -1, 0}, {1, 0, 0}, {0, 0, 1}}};
  std::vector<float> v;
  std::vector<unsigned short> idx;
  for (int f = 0; f < 6; ++f) {
    const unsigned short base = static_cast<unsigned short>(f * 4);
    for (int k = 0; k < 4; ++k) {
      const float a = (k == 0 || k == 3) ? -1.f : 1.f, b = (k < 2) ? -1.f : 1.f;
      for (int i = 0; i < 3; ++i) v.push_back(faces[f].n[i] + a * faces[f].u[i] + b * faces[f].v[i]);
      for (int i = 0; i < 3; ++i) v.push_back(faces[f].n[i]);
      v.push_back((a + 1) / 2);
      v.push_back(1 - (b + 1) / 2);
    }
    idx.insert(idx.end(), {base, static_cast<unsigned short>(base + 1), static_cast<unsigned short>(base + 2), base,
                           static_cast<unsigned short>(base + 2), static_cast<unsigned short>(base + 3)});
  }
  return Upload(v, idx);
}

// Cylinder (radius 1, y in [-1,1]) or cone (base radius 1 at y=-1, apex at y=+1).
Mesh MakeRound(bool cone, int seg) {
  std::vector<float> v;
  std::vector<unsigned short> idx;
  const float slope = cone ? 0.5f : 0.f;  // normal tilt for the cone side
  for (int s = 0; s <= seg; ++s) {
    const float a = 2 * kPi * s / seg, cx = std::cos(a), sz = std::sin(a);
    const float r_top = cone ? 0.f : 1.f;
    const float nl = std::sqrt(1.f + slope * slope);
    v.insert(v.end(), {cx, -1, sz, cx / nl, slope / nl, sz / nl, static_cast<float>(s) / seg, 1});
    v.insert(v.end(), {r_top * cx, 1, r_top * sz, cx / nl, slope / nl, sz / nl, static_cast<float>(s) / seg, 0});
  }
  for (int s = 0; s < seg; ++s) {
    const unsigned short a = static_cast<unsigned short>(s * 2);
    idx.insert(idx.end(), {a, static_cast<unsigned short>(a + 1), static_cast<unsigned short>(a + 2),
                           static_cast<unsigned short>(a + 2), static_cast<unsigned short>(a + 1),
                           static_cast<unsigned short>(a + 3)});
  }
  for (int cap = 0; cap < (cone ? 1 : 2); ++cap) {
    const float y = cap == 0 ? -1.f : 1.f, ny = cap == 0 ? -1.f : 1.f;
    const unsigned short c = static_cast<unsigned short>(v.size() / 8);
    v.insert(v.end(), {0, y, 0, 0, ny, 0, 0.5f, 0.5f});
    for (int s = 0; s <= seg; ++s) {
      const float a = 2 * kPi * s / seg;
      v.insert(v.end(), {std::cos(a), y, std::sin(a), 0, ny, 0, 0.5f + 0.5f * std::cos(a), 0.5f + 0.5f * std::sin(a)});
    }
    for (int s = 0; s < seg; ++s) {
      const unsigned short a = static_cast<unsigned short>(c + 1 + s);
      if (cap == 0) idx.insert(idx.end(), {c, static_cast<unsigned short>(a + 1), a});
      else idx.insert(idx.end(), {c, a, static_cast<unsigned short>(a + 1)});
    }
  }
  return Upload(v, idx);
}

// Torus lying in the XZ plane: major radius 1, tube radius 0.25.
Mesh MakeTorus(int major, int minor) {
  std::vector<float> v;
  std::vector<unsigned short> idx;
  const float tube = 0.25f;
  for (int i = 0; i <= major; ++i) {
    const float u = 2 * kPi * i / major;
    for (int j = 0; j <= minor; ++j) {
      const float w = 2 * kPi * j / minor;
      const float nx = std::cos(w) * std::cos(u), ny = std::sin(w), nz = std::cos(w) * std::sin(u);
      v.insert(v.end(), {(1 + tube * std::cos(w)) * std::cos(u), tube * std::sin(w), (1 + tube * std::cos(w)) * std::sin(u),
                         nx, ny, nz, static_cast<float>(i) / major, static_cast<float>(j) / minor});
    }
  }
  for (int i = 0; i < major; ++i)
    for (int j = 0; j < minor; ++j) {
      const unsigned short a = static_cast<unsigned short>(i * (minor + 1) + j), b = static_cast<unsigned short>(a + minor + 1);
      idx.insert(idx.end(), {a, b, static_cast<unsigned short>(a + 1), static_cast<unsigned short>(a + 1), b,
                             static_cast<unsigned short>(b + 1)});
    }
  return Upload(v, idx);
}

void DrawMesh(const Mesh& m, GLint a_pos, GLint a_nrm, GLint a_uv, GLint a_col) {
  // Built-in meshes carry no colours: a constant white attribute keeps the shader single-path.
  glDisableVertexAttribArray(a_col);
  glVertexAttrib4f(a_col, 1, 1, 1, 1);
  glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
  glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, m.ibo);
  const GLsizei stride = 8 * sizeof(float);
  glEnableVertexAttribArray(a_pos);
  glVertexAttribPointer(a_pos, 3, GL_FLOAT, GL_FALSE, stride, nullptr);
  glEnableVertexAttribArray(a_nrm);
  glVertexAttribPointer(a_nrm, 3, GL_FLOAT, GL_FALSE, stride,
                        reinterpret_cast<void*>(3 * sizeof(float)));
  glEnableVertexAttribArray(a_uv);
  glVertexAttribPointer(a_uv, 2, GL_FLOAT, GL_FALSE, stride,
                        reinterpret_cast<void*>(6 * sizeof(float)));
  glDrawElements(GL_TRIANGLES, m.index_count, GL_UNSIGNED_SHORT, nullptr);
}

}  // namespace

void VrPlayer::BuildMeshes() {
  sphere_ = MakeSphere(32, 48);
  disc_ = MakeDisc(96);
  quad_ = MakeQuad();
  box_ = MakeBox();
  cylinder_ = MakeRound(false, 48);
  cone_ = MakeRound(true, 48);
  torus_ = MakeTorus(64, 24);
}

void VrPlayer::OnSurfaceCreated(JNIEnv* /*env*/) {
  const int vs = LoadGLShader(GL_VERTEX_SHADER, kVertexShader);
  const int fs = LoadGLShader(GL_FRAGMENT_SHADER, kFragmentShader);
  program_ = glCreateProgram();
  glAttachShader(program_, vs);
  glAttachShader(program_, fs);
  glLinkProgram(program_);
  glUseProgram(program_);
  a_pos_ = glGetAttribLocation(program_, "a_Pos");
  a_nrm_ = glGetAttribLocation(program_, "a_Nrm");
  a_uv_ = glGetAttribLocation(program_, "a_UV");
  a_col_ = glGetAttribLocation(program_, "a_Col");
  u_model_ = glGetUniformLocation(program_, "u_Model");
  u_vp_ = glGetUniformLocation(program_, "u_VP");
  u_color_ = glGetUniformLocation(program_, "u_Color");
  u_lit_ = glGetUniformLocation(program_, "u_Lit");
  u_light_ = glGetUniformLocation(program_, "u_Light");
  u_inner_ = glGetUniformLocation(program_, "u_Inner");
  u_tex_ = glGetUniformLocation(program_, "u_Tex");
  u_use_tex_ = glGetUniformLocation(program_, "u_UseTex");
  u_sky_ = glGetUniformLocation(program_, "u_Sky");
  u_light_positions_ = glGetUniformLocation(program_, "u_LightPositions[0]");
  u_light_colors_ = glGetUniformLocation(program_, "u_LightColors[0]");
  u_light_directions_ = glGetUniformLocation(program_, "u_LightDirections[0]");
  u_light_cones_ = glGetUniformLocation(program_, "u_LightCones[0]");
  u_dynamic_count_ = glGetUniformLocation(program_, "u_DynamicCount");
  u_fog_enabled_ = glGetUniformLocation(program_, "u_FogEnabled");
  const GLuint particle_vertex = LoadGLShader(GL_VERTEX_SHADER, kParticleVertexShader);
  const GLuint particle_fragment = LoadGLShader(GL_FRAGMENT_SHADER, kParticleFragmentShader);
  particle_program_ = glCreateProgram();
  glAttachShader(particle_program_, particle_vertex);
  glAttachShader(particle_program_, particle_fragment);
  glLinkProgram(particle_program_);
  for (GLuint linked_program : {program_, particle_program_}) {
    GLint linked = GL_FALSE;
    glGetProgramiv(linked_program, GL_LINK_STATUS, &linked);
    if (linked != GL_TRUE) {
      char message[2048];
      glGetProgramInfoLog(linked_program, sizeof(message), nullptr, message);
      LOGE2("renderer shader link failed: %s", message);
    }
  }
  glDeleteShader(particle_vertex);
  glDeleteShader(particle_fragment);
  glDeleteShader(vs);
  glDeleteShader(fs);
  particle_position_ = glGetAttribLocation(particle_program_, "a_Position");
  particle_color_ = glGetAttribLocation(particle_program_, "a_Color");
  particle_size_ = glGetAttribLocation(particle_program_, "a_Size");
  particle_style_ = glGetAttribLocation(particle_program_, "a_Style");
  glGenBuffers(1, &particle_vbo_);
  GLfloat point_sizes[2];
  glGetFloatv(GL_ALIASED_POINT_SIZE_RANGE, point_sizes);
  point_size_max_ = point_sizes[1];
  BuildMeshes();

  // GL objects from a previous context are gone.
  tex_cache_.clear();
  model_cache_.clear();
  subtitle_tex_ = 0;
  applied_subtitle_.clear();
  CHECKGLERROR("OnSurfaceCreated");
}

void VrPlayer::SetScreenParams(int w, int h) {
  screen_width_ = w;
  screen_height_ = h;
  screen_params_changed_ = true;
}

void VrPlayer::OnPause() { CardboardHeadTracker_pause(head_tracker_); }

void VrPlayer::OnResume() {
  CardboardHeadTracker_resume(head_tracker_);
  device_params_changed_ = true;
  uint8_t* buffer;
  int size;
  CardboardQrCode_getSavedDeviceParams(&buffer, &size);
  // With no saved viewer we fall back to the SDK's built-in Cardboard v1 profile
  // (see UpdateDeviceParams); the settings menu can still scan a QR code.
  if (buffer != nullptr) CardboardQrCode_destroy(buffer);
}

void VrPlayer::SwitchViewer() { CardboardQrCode_scanQrCodeAndSaveDeviceParams(); }

void VrPlayer::Recenter() {
  std::lock_guard<std::mutex> lock(mu_);
  if (focus_node_ >= 0 && blink_phase_ == 0) StartBlinkLocked(rig_, true);
}

void VrPlayer::OnTriggerEvent() {
  std::lock_guard<std::mutex> lock(mu_);
  trigger_pending_ = true;
}

bool VrPlayer::UpdateDeviceParams() {
  if (!screen_params_changed_ && !device_params_changed_) return true;
  uint8_t* buffer = nullptr;
  int size = 0;
  CardboardQrCode_getSavedDeviceParams(&buffer, &size);
  const bool owns_buffer = size > 0;
  if (size == 0) {
    CardboardQrCode_getCardboardV1DeviceParams(&buffer, &size);
    if (size == 0) return false;
  }

  if (lens_distortion_ != nullptr) CardboardLensDistortion_destroy(lens_distortion_);
  lens_distortion_ = CardboardLensDistortion_create(buffer, size, screen_width_,
                                                    screen_height_);
  if (owns_buffer) CardboardQrCode_destroy(buffer);
  if (lens_distortion_ == nullptr) return false;
  GlSetup();

  CardboardDistortionRenderer_destroy(distortion_renderer_);
  const CardboardOpenGlEsDistortionRendererConfig config{kGlTexture2D};
  distortion_renderer_ = CardboardOpenGlEs2DistortionRenderer_create(&config);

  CardboardMesh left_mesh, right_mesh;
  CardboardLensDistortion_getDistortionMesh(lens_distortion_, kLeft, &left_mesh);
  CardboardLensDistortion_getDistortionMesh(lens_distortion_, kRight, &right_mesh);
  CardboardDistortionRenderer_setMesh(distortion_renderer_, &left_mesh, kLeft);
  CardboardDistortionRenderer_setMesh(distortion_renderer_, &right_mesh, kRight);

  CardboardLensDistortion_getEyeFromHeadMatrix(lens_distortion_, kLeft,
                                               eye_from_head_[0]);
  CardboardLensDistortion_getEyeFromHeadMatrix(lens_distortion_, kRight,
                                               eye_from_head_[1]);
  CardboardLensDistortion_getProjectionMatrix(lens_distortion_, kLeft, kZNear,
                                              kZFar, projection_[0]);
  CardboardLensDistortion_getProjectionMatrix(lens_distortion_, kRight, kZNear,
                                              kZFar, projection_[1]);
  screen_params_changed_ = false;
  device_params_changed_ = false;
  return true;
}

void VrPlayer::GlSetup() {
  if (framebuffer_ != 0) GlTeardown();
  glGenTextures(1, &texture_);
  glBindTexture(GL_TEXTURE_2D, texture_);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
  glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, screen_width_, screen_height_, 0,
               GL_RGB, GL_UNSIGNED_BYTE, 0);
  left_eye_tex_ = {texture_, 0, 0.5f, 1, 0};
  right_eye_tex_ = {texture_, 0.5f, 1, 1, 0};

  glGenRenderbuffers(1, &depth_rb_);
  glBindRenderbuffer(GL_RENDERBUFFER, depth_rb_);
  glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT16, screen_width_,
                        screen_height_);
  glGenFramebuffers(1, &framebuffer_);
  glBindFramebuffer(GL_FRAMEBUFFER, framebuffer_);
  glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                         texture_, 0);
  glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER,
                            depth_rb_);
  CHECKGLERROR("GlSetup");
}

void VrPlayer::GlTeardown() {
  if (framebuffer_ == 0) return;
  glDeleteRenderbuffers(1, &depth_rb_);
  depth_rb_ = 0;
  glDeleteFramebuffers(1, &framebuffer_);
  framebuffer_ = 0;
  glDeleteTextures(1, &texture_);
  texture_ = 0;
}

// ---- scene API ----------------------------------------------------------
void VrPlayer::SetScene(const std::vector<float>& d,
                        const std::vector<std::string>& tex_keys) {
  std::lock_guard<std::mutex> lock(mu_);
  nodes_.clear();
  emitters_.clear();
  particle_vertices_.clear();
  fog_[3] = 0;
  const size_t n = d.size() / kNodeStride;
  for (size_t i = 0; i < n; ++i) {
    const float* p = &d[i * kNodeStride];
    Node nd;
    nd.kind = static_cast<int>(p[0]);
    nd.pos[0] = p[1];
    nd.pos[1] = p[2];
    nd.pos[2] = p[3];
    nd.scale = p[4];
    for (int c = 0; c < 4; ++c) nd.color[c] = p[6 + c];
    nd.lit = p[10] > 0.5f;
    nd.parent = static_cast<int>(p[11]);
    nd.orbit_radius = p[12];
    nd.orbit_speed = p[13];
    nd.orbit_phase = p[14];
    nd.spin = p[15];
    nd.inner = p[16];
    nd.pick_id = static_cast<int>(p[17]);
    nd.tex = static_cast<int>(p[18]);
    nd.flags = static_cast<int>(p[19]);
    nd.tilt = p[20];
    nd.base_alpha = p[27] >= 0 ? p[27] : 1.f;
    for (int a = 0; a < 3; ++a) {
      nd.rot[a] = p[21 + a];
      nd.stretch[a] = p[24 + a] > 0 ? p[24 + a] : 1.f;
    }
    if (nd.kind == kEmitter) {
      nd.emitter.count = static_cast<int>(Bounded(p[32], 60, 1, kMaxEmitterParticles));
      nd.emitter.life = p[33];
      nd.emitter.speed = p[34];
      nd.emitter.gravity = p[35];
      nd.emitter.drag = p[36];
      nd.emitter.spread = p[37];
      for (int axis = 0; axis < 3; axis++) {
        nd.emitter.direction[axis] = p[38 + axis];
        nd.emitter.extent[axis] = p[42 + axis];
      }
      nd.emitter.shape = static_cast<int>(Bounded(p[41], 0, 0, 4));
      nd.emitter.size = {p[45], p[46]};
      for (int channel = 0; channel < 4; channel++) {
        nd.emitter.start_color[channel] = p[47 + channel];
        nd.emitter.end_color[channel] = p[51 + channel];
      }
      nd.emitter.additive = p[55] > 0.5f;
      nd.emitter.loop = p[56] > 0.5f;
      nd.emitter.texture = static_cast<int>(Bounded(p[57], 0, 0, 4));
    } else if (nd.kind == kDynamicLight) {
      nd.light.spot = p[32] > 0.5f;
      for (int axis = 0; axis < 3; axis++) {
        nd.light.color[axis] = Bounded(p[33 + axis], 1, 0, 1);
        nd.light.direction[axis] = p[40 + axis];
      }
      nd.light.direction = UnitDirection(nd.light.direction);
      nd.light.intensity = Bounded(p[36], 1, 0, 10);
      nd.light.range = Bounded(p[37], 8, 0.5f, 60);
      nd.light.angle = Bounded(p[38], 0.610865f, 0.087266f, 1.396263f);
      nd.light.softness = Bounded(p[39], 0.3f, 0, 1);
      nd.light.flicker = static_cast<int>(Bounded(p[43], 0, 0, 4));
      nd.light.beam = p[44] > 0.5f;
      nd.light.aim = static_cast<int>(Bounded(p[45], -1, -1, static_cast<float>(n)));
    }
    nodes_.push_back(nd);
  }
  int particle_total = 0, emitter_total = 0, light_total = 0;
  for (Node& node : nodes_) {
    if (node.kind == kEmitter) {
      if (++emitter_total > kMaxEmitters) node.kind = kEffectGroup;
      else particle_total += node.emitter.count;
    } else if (node.kind == kDynamicLight && ++light_total > kMaxLights) {
      node.kind = kEffectGroup;
    }
  }
  for (size_t index = 0; index < nodes_.size(); index++) {
    Node& node = nodes_[index];
    if (node.kind != kEmitter) continue;
    if (particle_total > kMaxParticles)
      node.emitter.count = std::max(1, node.emitter.count * kMaxParticles / particle_total);
    ParticleEmitter emitter;
    emitter.Initialize(static_cast<int>(index), node.emitter);
    emitters_.push_back(std::move(emitter));
  }
  for (Node& n : nodes_)
    if ((n.flags & kLabel) && n.parent >= 0 && n.parent < static_cast<int>(nodes_.size())) nodes_[n.parent].has_label = true;
  tex_keys_ = tex_keys;
  anims_.clear();
  tweens_.clear();
  highlight_ = -1;
  gaze_target_ = -1;
  gaze_progress_ = 0;
  LOGI("scene set: %zu nodes, %zu textures", nodes_.size(), tex_keys_.size());
}

void VrPlayer::SetFog(const std::vector<float>& data) {
  std::lock_guard<std::mutex> lock(mu_);
  fog_ = {0.7f, 0.75f, 0.8f, 0, 0, 0, 0};
  if (data.size() != fog_.size()) return;
  for (int channel = 0; channel < 3; channel++) fog_[channel] = Bounded(data[channel], 0.7f, 0, 1);
  fog_[3] = Bounded(data[3], 0, 0, 0.5f);
  fog_[4] = Bounded(data[4], 0, -10000, 10000);
  fog_[5] = Bounded(data[5], 0, 0, 5);
  fog_[6] = Bounded(data[6], 0, 0, 100);
}

void VrPlayer::Burst(int node) {
  std::lock_guard<std::mutex> lock(mu_);
  if (node < 0 || node >= static_cast<int>(nodes_.size())) return;
  for (ParticleEmitter& emitter : emitters_) {
    int ancestor = emitter.node;
    for (size_t depth = 0; depth < nodes_.size() && ancestor >= 0
        && ancestor < static_cast<int>(nodes_.size()); depth++) {
      if (ancestor == node) { emitter.Burst(); break; }
      ancestor = nodes_[ancestor].parent;
    }
  }
  for (size_t index = 0; index < nodes_.size(); index++) {
    if (nodes_[index].kind != kDynamicLight) continue;
    int ancestor = static_cast<int>(index);
    for (size_t depth = 0; depth < nodes_.size() && ancestor >= 0
        && ancestor < static_cast<int>(nodes_.size()); depth++) {
      if (ancestor == node) { nodes_[index].light.burst_age = 0; break; }
      ancestor = nodes_[ancestor].parent;
    }
  }
}

void VrPlayer::StartBlinkLocked(const float* target, bool align) {
  LOGI("viewpoint change (blink) to (%.1f, %.1f, %.1f) align=%d", target[0], target[1], target[2], align ? 1 : 0);
  blink_phase_ = 1;
  blink_t0_ = NowSeconds();
  std::memcpy(blink_target_, target, sizeof(blink_target_));
  blink_align_ = align;
}

// Viewpoint changes never slide the camera. `seconds < 0` marks the first viewpoint of a scene: it is placed
// under a fade-in and the world is aligned once with the viewer's current heading. Later changes of more than
// 0.75 m are a short fade-out / jump / fade-in; smaller ones are ignored. `follow` is a static offset from the
// node's position now (the camera never rides a moving object).
void VrPlayer::MoveCamera(float x, float y, float z, int focus, float seconds, int follow) {
  std::lock_guard<std::mutex> lock(mu_);
  float tgt[3] = {x, y, z};
  if (follow >= 0 && follow < static_cast<int>(nodes_.size()))
    for (int i = 0; i < 3; ++i) tgt[i] += nodes_[follow].world[i];
  focus_node_ = focus;
  const float dx = tgt[0] - rig_[0], dy = tgt[1] - rig_[1], dz = tgt[2] - rig_[2];
  const float dist = std::sqrt(dx * dx + dy * dy + dz * dz);
  if (seconds < 0) {
    std::memcpy(rig_, tgt, sizeof(rig_));
    yaw_pending_ = focus >= 0;
    fade_ = 1.f;
    blink_phase_ = 2;
    blink_t0_ = NowSeconds();
  } else if (dist > 0.75f) {
    StartBlinkLocked(tgt, false);
  }
}

void VrPlayer::SetAnimations(const std::vector<float>& d) {
  std::lock_guard<std::mutex> lock(mu_);
  anims_.clear();
  size_t i = 0;
  while (i < d.size()) {
    AnimRec r;
    r.type = static_cast<int>(d[i]);
    if (r.type == 0 && i + 13 <= d.size()) {
      r.node = static_cast<int>(d[i + 1]);
      r.prop = static_cast<int>(d[i + 2]);
      r.loop = static_cast<int>(d[i + 3]);
      r.ease = static_cast<int>(d[i + 4]);
      r.seconds = std::max(0.05f, d[i + 5]);
      r.delay = d[i + 6];
      for (int k = 0; k < 3; ++k) { r.from[k] = d[i + 7 + k]; r.to[k] = d[i + 10 + k]; }
      i += 13;
    } else if (r.type == 1 && i + 6 <= d.size()) {
      r.node = static_cast<int>(d[i + 1]);
      r.loop = static_cast<int>(d[i + 2]);
      r.seconds = std::max(0.05f, d[i + 3]);
      r.delay = d[i + 4];  // for paths: phase offset in 0..1 of the route
      const size_t n = static_cast<size_t>(d[i + 5]);
      if (i + 6 + 3 * n > d.size()) break;
      r.pts.assign(d.begin() + i + 6, d.begin() + i + 6 + 3 * n);
      i += 6 + 3 * n;
    } else {
      break;
    }
    anims_.push_back(std::move(r));
  }
  LOGI("animations set: %zu records", anims_.size());
}

void VrPlayer::TweenNode(int node, int prop, float x, float y, float z, float seconds) {
  std::lock_guard<std::mutex> lock(mu_);
  if (node < 0 || node >= static_cast<int>(nodes_.size())) return;
  Node& n = nodes_[node];
  Tween tw;
  tw.node = node;
  tw.prop = prop;
  if (prop == 0) { std::memcpy(tw.from, n.pos, sizeof(tw.from)); }
  else if (prop == 1) { tw.from[0] = n.scale; }
  else { tw.from[0] = n.base_alpha; }
  tw.to[0] = x; tw.to[1] = y; tw.to[2] = z;
  tw.start = NowSeconds();
  tw.dur = std::max(0.0f, seconds);
  // a newer tween on the same property replaces the old one
  tweens_.erase(std::remove_if(tweens_.begin(), tweens_.end(), [&](const Tween& o) { return o.node == node && o.prop == prop; }),
                tweens_.end());
  tweens_.push_back(tw);
}

void VrPlayer::SetFixedPose(bool on, float seconds) {
  std::lock_guard<std::mutex> lock(mu_);
  fixed_pose_ = on;
  fixed_until_ = NowSeconds() + std::max(5.f, seconds);
}

void VrPlayer::SetHighlight(int node) {
  std::lock_guard<std::mutex> lock(mu_);
  highlight_ = node;
}

void VrPlayer::SetSubtitle(const std::string& text) {
  std::lock_guard<std::mutex> lock(mu_);
  subtitle_ = text;
}

void VrPlayer::SetGazeEnabled(bool enabled) {
  std::lock_guard<std::mutex> lock(mu_);
  gaze_enabled_ = enabled;
  gaze_target_ = -1;
  gaze_progress_ = 0;
}

// ---- textures -----------------------------------------------------------
GLuint VrPlayer::GetTexture(JNIEnv* env, const std::string& key, float* aspect) {
  auto it = tex_cache_.find(key);
  if (it != tex_cache_.end()) {
    if (aspect) *aspect = it->second.aspect;
    return it->second.id;
  }
  CachedTexture ct;
  jstring jkey = env->NewStringUTF(key.c_str());
  jintArray res = static_cast<jintArray>(
      env->CallObjectMethod(activity_, load_texture_mid_, jkey));
  env->DeleteLocalRef(jkey);
  if (res != nullptr) {
    jint v[3];
    env->GetIntArrayRegion(res, 0, 3, v);
    ct.id = static_cast<GLuint>(v[0]);
    ct.aspect = v[2] > 0 ? static_cast<float>(v[1]) / v[2] : 1.f;
    env->DeleteLocalRef(res);
  } else {
    LOGE2("texture failed: %s", key.c_str());
  }
  tex_cache_[key] = ct;
  if (aspect) *aspect = ct.aspect;
  return ct.id;
}

const Model* VrPlayer::GetModel(JNIEnv* env, const std::string& key) {
  auto it = model_cache_.find(key);
  if (it != model_cache_.end()) return it->second.ok ? &it->second : nullptr;
  Model m;
  jstring jkey = env->NewStringUTF(key.c_str());
  jstring jpath = static_cast<jstring>(env->CallObjectMethod(activity_, model_path_mid_, jkey));
  env->DeleteLocalRef(jkey);
  if (jpath != nullptr) {
    const char* cpath = env->GetStringUTFChars(jpath, nullptr);
    LoadGlb(cpath, &m);
    env->ReleaseStringUTFChars(jpath, cpath);
    env->DeleteLocalRef(jpath);
  }
  auto& slot = model_cache_[key];
  slot = m;
  return slot.ok ? &slot : nullptr;
}

// ---- per-frame ----------------------------------------------------------
namespace {
float Ease(float u, int ease) {
  u = std::min(1.f, std::max(0.f, u));
  return ease == 1 ? u * u * (3 - 2 * u) : u;
}
// Maps elapsed time to a 0..1 phase for the three loop modes (0 once, 1 loop, 2 ping-pong).
float Phase(float el, float seconds, int loop) {
  if (el < 0) return 0;
  const float u = el / seconds;
  if (loop == 0) return std::min(1.f, u);
  if (loop == 1) return u - std::floor(u);
  const float m = std::fmod(u, 2.f);
  return m <= 1.f ? m : 2.f - m;
}
}  // namespace

void VrPlayer::UpdateWorld(float t) {
  const double now = NowSeconds();
  const float dt = last_frame_ > 0 ? static_cast<float>(std::min(0.2, now - last_frame_)) : 0.f;
  last_frame_ = now;
  // step-driven tweens modify the node's base state
  for (size_t k = 0; k < tweens_.size();) {
    Tween& tw = tweens_[k];
    const float u = tw.dur > 0 ? static_cast<float>(std::min(1.0, (now - tw.start) / tw.dur)) : 1.f;
    const float e = u * u * (3 - 2 * u);
    Node& n = nodes_[tw.node];
    if (tw.prop == 0) { for (int a = 0; a < 3; ++a) n.pos[a] = tw.from[a] + (tw.to[a] - tw.from[a]) * e; }
    else if (tw.prop == 1) { n.scale = tw.from[0] + (tw.to[0] - tw.from[0]) * e; }
    else { n.base_alpha = tw.from[0] + (tw.to[0] - tw.from[0]) * e; }
    if (u >= 1.f) tweens_.erase(tweens_.begin() + k); else ++k;
  }
  for (Node& n : nodes_) {
    n.anim_off[0] = n.anim_off[1] = n.anim_off[2] = 0;
    n.anim_rot[0] = n.anim_rot[1] = n.anim_rot[2] = 0;
    n.anim_scale = 1;
    n.anim_alpha = 1;
  }
  std::vector<const AnimRec*> paths(nodes_.size(), nullptr);
  for (const AnimRec& r : anims_) {
    if (r.node < 0 || r.node >= static_cast<int>(nodes_.size())) continue;
    Node& n = nodes_[r.node];
    if (r.type == 1) { paths[r.node] = &r; continue; }
    const float u = Ease(Phase(t - r.delay, r.seconds, r.loop), r.ease);
    float v[3];
    for (int a = 0; a < 3; ++a) v[a] = r.from[a] + (r.to[a] - r.from[a]) * u;
    if (r.prop == 0) { for (int a = 0; a < 3; ++a) n.anim_off[a] += v[a]; }
    else if (r.prop == 1) { n.anim_scale *= v[0]; }
    else if (r.prop == 2) { for (int a = 0; a < 3; ++a) n.anim_rot[a] += v[a]; }
    else { n.anim_alpha *= v[0]; }
  }
  // Parents always have a lower index than children.
  for (size_t i = 0; i < nodes_.size(); ++i) {
    Node& n = nodes_[i];
    float base[3] = {0, 0, 0};
    float parent_alpha = 1;
    if (n.parent >= 0 && n.parent < static_cast<int>(i)) {
      std::memcpy(base, nodes_[n.parent].world, sizeof(base));
      parent_alpha = nodes_[n.parent].alpha;
    }
    if (n.flags & kLabel) {  // an auto-label shows only for the highlighted or looked-at node
      const bool want = n.parent >= 0 && (n.parent == highlight_ || n.parent == looked_);
      n.fade += (want ? 1.f : -1.f) * std::min(1.f, dt * 5.f);
      n.fade = std::min(1.f, std::max(0.f, n.fade));
    } else {
      n.fade = 1.f;
    }
    n.alpha = n.base_alpha * n.anim_alpha * parent_alpha * n.fade;
    float ox = 0, oz = 0;
    if (n.orbit_radius > 0) {
      const float a = n.orbit_phase + n.orbit_speed * t;
      ox = n.orbit_radius * std::cos(a);
      oz = n.orbit_radius * std::sin(a);
    }
    float px = n.pos[0], py = n.pos[1], pz = n.pos[2];
    if (const AnimRec* pr = paths[i]) {  // follow a polyline (ping-pong or loop)
      const size_t cnt = pr->pts.size() / 3;
      if (cnt >= 2) {
        const float u = Phase(t + pr->delay * pr->seconds, pr->seconds, pr->loop) * static_cast<float>(pr->loop == 1 ? cnt : cnt - 1);
        const size_t s0 = std::min(static_cast<size_t>(u), cnt - 1), s1 = pr->loop == 1 ? (s0 + 1) % cnt : std::min(s0 + 1, cnt - 1);
        const float f = u - std::floor(u);
        px = pr->pts[s0 * 3] + (pr->pts[s1 * 3] - pr->pts[s0 * 3]) * f;
        py = pr->pts[s0 * 3 + 1] + (pr->pts[s1 * 3 + 1] - pr->pts[s0 * 3 + 1]) * f;
        pz = pr->pts[s0 * 3 + 2] + (pr->pts[s1 * 3 + 2] - pr->pts[s0 * 3 + 2]) * f;
      }
    }
    n.world[0] = base[0] + px + ox + n.anim_off[0];
    n.world[1] = base[1] + py + n.anim_off[1];
    n.world[2] = base[2] + pz + oz + n.anim_off[2];
  }
}

int VrPlayer::PickNode(const float* g, const float* eye) {
  int best = -1;
  float best_angle = 1e9f;
  for (size_t i = 0; i < nodes_.size(); ++i) {
    const Node& n = nodes_[i];
    if (n.pick_id < 0 || n.alpha < 0.3f) continue;
    float d[3] = {n.world[0] - eye[0], n.world[1] - eye[1], n.world[2] - eye[2]};
    const float dist = std::sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    if (dist < 1e-3f) continue;
    const float c = (d[0] * g[0] + d[1] * g[1] + d[2] * g[2]) / dist;
    const float angle = std::acos(std::min(1.f, std::max(-1.f, c)));
    const float limit =
        std::max(kMinPickAngle, std::asin(std::min(1.f, n.scale / dist)));
    if (angle < limit && angle < best_angle) {
      best = static_cast<int>(i);
      best_angle = angle;
    }
  }
  return best;
}

void VrPlayer::ApplyFog(GLuint program, const float* eye, bool enabled) {
  glUniform4fv(glGetUniformLocation(program, "u_FogColorDensity"), 1, fog_.data());
  glUniform3fv(glGetUniformLocation(program, "u_FogShape"), 1, fog_.data() + 4);
  glUniform3fv(glGetUniformLocation(program, "u_FogEye"), 1, eye);
  glUniform1f(glGetUniformLocation(program, "u_FogEnabled"), enabled ? 1.f : 0.f);
}

void VrPlayer::UpdateEffects(float seconds, float delta) {
  for (Node& node : nodes_) {
    if (node.kind == kDynamicLight) node.light.burst_age += delta;
  }
  particle_vertices_.clear();
  particle_vertices_.reserve(kMaxParticles * 9);
  alpha_particle_count_ = additive_particle_count_ = 0;
  std::vector<std::array<float, 9>> transparent;
  for (ParticleEmitter& emitter : emitters_) {
    const Node& node = nodes_[emitter.node];
    emitter.Advance(delta, node.alpha > 0.01f);
    if (node.alpha <= 0.01f) continue;
    const auto& config = emitter.config;
    const M4 rotation = Mul(Mul(RotZ(node.tilt), RotY(node.spin * seconds)),
        Mul(Mul(RotX(node.rot[0] + node.anim_rot[0]), RotY(node.rot[1] + node.anim_rot[1])),
            RotZ(node.rot[2] + node.anim_rot[2])));
    const float scale = node.scale * node.anim_scale;
    for (const Particle& particle : emitter.particles) {
      if (!particle.active) continue;
      const float fraction = std::clamp(particle.age / config.life, 0.f, 1.f);
      const auto local = emitter.Position(particle);
      std::array<float, 9> vertex{};
      for (int axis = 0; axis < 3; axis++) {
        vertex[axis] = node.world[axis];
        for (int component = 0; component < 3; component++)
          vertex[axis] += rotation[component * 4 + axis] * local[component] * scale * node.stretch[component];
      }
      for (int channel = 0; channel < 4; channel++)
        vertex[3 + channel] = config.start_color[channel]
            + (config.end_color[channel] - config.start_color[channel]) * fraction;
      vertex[6] *= node.alpha * node.color[3];
      vertex[7] = std::abs(scale) * (config.size[0] + (config.size[1] - config.size[0]) * fraction);
      vertex[8] = static_cast<float>(config.texture);
      if (vertex[6] <= 0.001f || vertex[7] <= 0) continue;
      if (!config.additive) transparent.push_back(vertex);
      else {
        particle_vertices_.insert(particle_vertices_.end(), vertex.begin(), vertex.end());
        additive_particle_count_++;
      }
    }
  }
  auto distance_squared = [this](const std::array<float, 9>& vertex) {
    float distance = 0;
    for (int axis = 0; axis < 3; axis++) distance += (vertex[axis] - rig_[axis]) * (vertex[axis] - rig_[axis]);
    return distance;
  };
  std::sort(transparent.begin(), transparent.end(), [&](const auto& first, const auto& second) {
    return distance_squared(first) > distance_squared(second);
  });
  alpha_particle_count_ = static_cast<int>(transparent.size());
  for (const auto& vertex : transparent)
    particle_vertices_.insert(particle_vertices_.end(), vertex.begin(), vertex.end());
  glBindBuffer(GL_ARRAY_BUFFER, particle_vbo_);
  glBufferData(GL_ARRAY_BUFFER, particle_vertices_.size() * sizeof(float),
      particle_vertices_.empty() ? nullptr : particle_vertices_.data(), GL_STREAM_DRAW);
}

void VrPlayer::DrawParticles(const float* vp, const float* eye, float projection_scale) {
  if (particle_vertices_.empty()) return;
  glUseProgram(particle_program_);
  glUniformMatrix4fv(glGetUniformLocation(particle_program_, "u_VP"), 1, GL_FALSE, vp);
  glUniform1f(glGetUniformLocation(particle_program_, "u_ProjectionScale"), projection_scale);
  glUniform1f(glGetUniformLocation(particle_program_, "u_MaxPointSize"), point_size_max_);
  ApplyFog(particle_program_, eye, true);
  glBindBuffer(GL_ARRAY_BUFFER, particle_vbo_);
  glDisableVertexAttribArray(a_pos_);
  glDisableVertexAttribArray(a_nrm_);
  glDisableVertexAttribArray(a_uv_);
  glDisableVertexAttribArray(a_col_);
  const GLsizei stride = 9 * sizeof(float);
  glEnableVertexAttribArray(particle_position_);
  glVertexAttribPointer(particle_position_, 3, GL_FLOAT, GL_FALSE, stride, nullptr);
  glEnableVertexAttribArray(particle_color_);
  glVertexAttribPointer(particle_color_, 4, GL_FLOAT, GL_FALSE, stride, reinterpret_cast<void*>(3 * sizeof(float)));
  glEnableVertexAttribArray(particle_size_);
  glVertexAttribPointer(particle_size_, 1, GL_FLOAT, GL_FALSE, stride, reinterpret_cast<void*>(7 * sizeof(float)));
  glEnableVertexAttribArray(particle_style_);
  glVertexAttribPointer(particle_style_, 1, GL_FLOAT, GL_FALSE, stride, reinterpret_cast<void*>(8 * sizeof(float)));
  glDepthMask(GL_FALSE);
  glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
  glUniform1f(glGetUniformLocation(particle_program_, "u_Additive"), 0);
  if (alpha_particle_count_) glDrawArrays(GL_POINTS, additive_particle_count_, alpha_particle_count_);
  glBlendFunc(GL_SRC_ALPHA, GL_ONE);
  glUniform1f(glGetUniformLocation(particle_program_, "u_Additive"), 1);
  if (additive_particle_count_) glDrawArrays(GL_POINTS, 0, additive_particle_count_);
  glDisableVertexAttribArray(particle_position_);
  glDisableVertexAttribArray(particle_color_);
  glDisableVertexAttribArray(particle_size_);
  glDisableVertexAttribArray(particle_style_);
  glDepthMask(GL_TRUE);
  glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
  glUseProgram(program_);
}

void VrPlayer::DrawLightBeams(const float* vp, const float* eye, float seconds) {
  glDepthMask(GL_FALSE);
  for (const Node& node : nodes_) {
    if (node.kind != kDynamicLight || !node.light.spot || !node.light.beam || node.alpha < 0.01f) continue;
    const LightConfig& light = node.light;
    auto direction = light.direction;
    if (light.aim >= 0 && light.aim < static_cast<int>(nodes_.size())) {
      for (int axis = 0; axis < 3; axis++) direction[axis] = nodes_[light.aim].world[axis] - node.world[axis];
      direction = UnitDirection(direction);
    }
    const auto right = UnitDirection(std::abs(direction[1]) < 0.99f
        ? std::array<float, 3>{direction[2], 0, -direction[0]} : std::array<float, 3>{1, 0, 0});
    M4 orientation = Identity();
    for (int axis = 0; axis < 3; axis++) {
      orientation[axis] = right[axis];
      orientation[4 + axis] = -direction[axis];
    }
    orientation[8] = right[1] * -direction[2] - right[2] * -direction[1];
    orientation[9] = right[2] * -direction[0] - right[0] * -direction[2];
    orientation[10] = right[0] * -direction[1] - right[1] * -direction[0];
    const float radius = light.range * std::tan(light.angle);
    const M4 model = Mul(Mul(Translate(node.world[0] + direction[0] * light.range * 0.5f,
        node.world[1] + direction[1] * light.range * 0.5f,
        node.world[2] + direction[2] * light.range * 0.5f), orientation),
        Scale(radius, light.range * 0.5f, radius));
    const float color[4] = {light.color[0], light.color[1], light.color[2],
        std::min(0.2f, light.intensity * 0.04f) * node.alpha * LightModulation(light, seconds)};
    glUniformMatrix4fv(u_model_, 1, GL_FALSE, model.data());
    glUniformMatrix4fv(u_vp_, 1, GL_FALSE, vp);
    glUniform4fv(u_color_, 1, color);
    glUniform1f(u_lit_, 0);
    glUniform1f(u_use_tex_, 0);
    glUniform1f(u_inner_, 0);
    glUniform1f(u_fog_enabled_, 1);
    glDisable(GL_CULL_FACE);
    DrawMesh(cone_, a_pos_, a_nrm_, a_uv_, a_col_);
  }
  glDepthMask(GL_TRUE);
  (void)eye;
}

void VrPlayer::DrawNode(const Node& n, const float* vp, const float* eye,
                        const float* yaw_inv, bool hud, float t, float pulse) {
  (void)yaw_inv;
  (void)eye;
  if (n.alpha < 0.01f || n.kind == kEmitter || n.kind == kDynamicLight || n.kind == kEffectGroup) return;
  const bool text = n.tex >= 0 && n.tex < static_cast<int>(tex_keys_.size())
      && (tex_keys_[n.tex].rfind("label:", 0) == 0 || tex_keys_[n.tex].rfind("option:", 0) == 0);
  glUniform1f(u_fog_enabled_, !hud && !text && !(n.flags & kLabel) && n.kind != kSky ? 1.f : 0.f);
  float aspect = 1;
  GLuint tex = 0;
  JNIEnv* env;
  vm_->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6);
  if (n.kind != kModel && n.tex >= 0 && n.tex < static_cast<int>(tex_keys_.size()))
    tex = GetTexture(env, tex_keys_[n.tex], &aspect);

  M4 model;
  if (n.kind == kSky) {
    model = Mul(Translate(eye[0], eye[1], eye[2]), Scale(200, 200, 200));
  } else if (n.kind == kQuad && (n.flags & kBillboard)) {
    // Face the eye (yaw only keeps text upright).
    const float dx = eye[0] - n.world[0], dz = eye[2] - n.world[2];
    const float yaw = std::atan2(dx, dz);
    model = Mul(Mul(Translate(n.world[0], n.world[1], n.world[2]), RotY(yaw)),
                Scale(n.scale * aspect * pulse, n.scale * pulse, 1));
  } else if (n.kind == kQuad) {
    model = Mul(Translate(n.world[0], n.world[1], n.world[2]),
                Scale(n.scale * aspect * pulse, n.scale * pulse, 1));
  } else {
    // Generic solid or flat disc: T * Rz(tilt) * Ry(spin) * Rx*Ry*Rz(rot) * [disc flat] * S.
    M4 m = Mul(Translate(n.world[0], n.world[1], n.world[2]), RotZ(n.tilt));
    m = Mul(Mul(m, RotY(n.spin * t)), Mul(Mul(RotX(n.rot[0]), RotY(n.rot[1])), RotZ(n.rot[2])));
    if (n.kind == kDisc) m = Mul(m, RotX(-kPi / 2));
    m = Mul(m, Mul(Mul(RotX(n.anim_rot[0]), RotY(n.anim_rot[1])), RotZ(n.anim_rot[2])));
    const float s = n.scale * n.anim_scale * pulse;
    model = Mul(m, Scale(s * n.stretch[0], s * n.stretch[1], s * n.stretch[2]));
  }
  (void)hud;
  glUniformMatrix4fv(u_model_, 1, GL_FALSE, model.data());
  glUniformMatrix4fv(u_vp_, 1, GL_FALSE, vp);
  {
    const float cc[4] = {n.color[0], n.color[1], n.color[2], n.color[3] * n.alpha};
    glUniform4fv(u_color_, 1, cc);
  }
  glUniform1f(u_lit_, n.lit ? 1.f : 0.f);
  glUniform1f(u_inner_, n.kind == kDisc ? n.inner : 0.f);
  glUniform1f(u_use_tex_, tex ? 1.f : 0.f);
  if (tex) {
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(u_tex_, 0);
  }
  if (n.kind == kModel) {
    const Model* mdl = (n.tex >= 0 && n.tex < static_cast<int>(tex_keys_.size())) ? GetModel(env, tex_keys_[n.tex]) : nullptr;
    if (!mdl) return;
    glDisable(GL_CULL_FACE);
    // Node transforms at time t (embedded glTF animation), then fit the model in a unit box.
    std::vector<Mat4> world;
    EvalModel(*mdl, t, &world);
    const M4 fit = Mul(Scale(mdl->scale, mdl->scale, mdl->scale), Translate(-mdl->center[0], -mdl->center[1], -mdl->center[2]));
    const M4 base = Mul(model, fit);
    const GLsizei stride = 12 * sizeof(float);
    for (const ModelPrim& pr : mdl->prims) {
      const M4 pm = Mul(base, world[pr.node]);
      glUniformMatrix4fv(u_model_, 1, GL_FALSE, pm.data());
      float col[4] = {n.color[0] * pr.color[0], n.color[1] * pr.color[1], n.color[2] * pr.color[2], n.color[3] * pr.color[3] * n.alpha};
      glUniform4fv(u_color_, 1, col);
      glUniform1f(u_use_tex_, pr.texture ? 1.f : 0.f);
      if (pr.texture) {
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, pr.texture);
      }
      glBindBuffer(GL_ARRAY_BUFFER, pr.vbo);
      glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, pr.ibo);
      glEnableVertexAttribArray(a_pos_);
      glVertexAttribPointer(a_pos_, 3, GL_FLOAT, GL_FALSE, stride, nullptr);
      glEnableVertexAttribArray(a_nrm_);
      glVertexAttribPointer(a_nrm_, 3, GL_FLOAT, GL_FALSE, stride, reinterpret_cast<void*>(3 * sizeof(float)));
      glEnableVertexAttribArray(a_uv_);
      glVertexAttribPointer(a_uv_, 2, GL_FLOAT, GL_FALSE, stride, reinterpret_cast<void*>(6 * sizeof(float)));
      glEnableVertexAttribArray(a_col_);
      glVertexAttribPointer(a_col_, 4, GL_FLOAT, GL_FALSE, stride, reinterpret_cast<void*>(8 * sizeof(float)));
      glDrawElements(GL_TRIANGLES, pr.index_count, GL_UNSIGNED_SHORT, nullptr);
    }
    return;
  }
  const Mesh& mesh = n.kind == kQuad ? quad_ : n.kind == kDisc ? disc_ : n.kind == kBox ? box_
                     : n.kind == kCylinder ? cylinder_ : n.kind == kCone ? cone_
                     : n.kind == kTorus ? torus_ : sphere_;
  if (n.kind == kSky) {
    glDisable(GL_CULL_FACE);
    glDisable(GL_DEPTH_TEST);
  } else if (n.kind == kDisc || n.kind == kQuad) {
    glDisable(GL_CULL_FACE);
  } else {
    glDisable(GL_CULL_FACE);
  }
  DrawMesh(mesh, a_pos_, a_nrm_, a_uv_, a_col_);
  if (n.kind == kSky) glEnable(GL_DEPTH_TEST);
}

void VrPlayer::OnDrawFrame(JNIEnv* env) {
  if (!UpdateDeviceParams()) return;
  const double now = NowSeconds();
  const float t = static_cast<float>(now - t0_);

  // Head pose.
  float position[3];
  float orientation[4];
  CardboardHeadTracker_getPose(
      head_tracker_, GetBootTimeNano() + kPredictionTimeWithoutVsyncNanos,
      kLandscapeLeft, position, orientation);
  if (fixed_pose_ && NowSeconds() > fixed_until_) fixed_pose_ = false;  // debug mode expires
  if (fixed_pose_) {
    orientation[0] = orientation[1] = orientation[2] = 0.f;
    orientation[3] = 1.f;
  }
  const M4 pose_rot = FromQuat(orientation);
  const float fwd_in[3] = {0, 0, -1};
  float f_tracker[3];
  RotateInverse(pose_rot, fwd_in, f_tracker);

  static double last_log = 0;
  if (t - last_log > 2.0) {
    last_log = t;
    LOGI("head forward=(%.2f,%.2f,%.2f) yaw=%.2f rig=(%.1f,%.1f,%.1f) nodes=%zu", f_tracker[0],
         f_tracker[1], f_tracker[2], yaw_, rig_[0], rig_[1], rig_[2], nodes_.size());
    for (size_t i = 0; i < nodes_.size() && i < 4; ++i)
      LOGI("  node %zu kind=%d world=(%.1f,%.1f,%.1f) scale=%.2f stretch=(%.1f,%.1f,%.1f) a=%.1f", i, nodes_[i].kind,
           nodes_[i].world[0], nodes_[i].world[1], nodes_[i].world[2], nodes_[i].scale, nodes_[i].stretch[0],
           nodes_[i].stretch[1], nodes_[i].stretch[2], nodes_[i].color[3]);
  }

  int gaze_fire = -1;
  bool open_menu = false;
  float menu_rig[3] = {0, 0, 0}, menu_heading[2] = {0, -1};
  M4 head_view;
  float eye_pos[3];
  float subtitle_aspect = 1;
  GLuint subtitle_tex = 0;
  float arrow_alpha = 0, arrow_theta = 0, fade_now = 0;
  {
    std::lock_guard<std::mutex> lock(mu_);
    const float effects_delta = last_frame_ > 0
        ? static_cast<float>(std::clamp(now - last_frame_, 0.0, 0.2)) : 0.f;
    UpdateWorld(t);
    UpdateEffects(t, effects_delta);

    // Comfort camera: a viewpoint change is fade out -> jump -> fade in.
    if (blink_phase_ == 1) {
      const float u = static_cast<float>((now - blink_t0_) / 0.22);
      fade_ = std::min(1.f, u);
      if (u >= 1.f) {
        std::memcpy(rig_, blink_target_, sizeof(rig_));
        if (blink_align_) yaw_pending_ = true;
        blink_phase_ = 2;
        blink_t0_ = now;
      }
    } else if (blink_phase_ == 2) {
      const float u = static_cast<float>((now - blink_t0_) / 0.40);
      fade_ = 1.f - std::min(1.f, u);
      if (u >= 1.f) {
        blink_phase_ = 0;
        fade_ = 0;
      }
    }

    // Align the world yaw with the viewer's heading: only at scene start or on an explicit tap, hidden by the fade.
    if (yaw_pending_ && focus_node_ >= 0 &&
        focus_node_ < static_cast<int>(nodes_.size())) {
      const Node& fn = nodes_[focus_node_];
      const float dx = fn.world[0] - rig_[0], dz = fn.world[2] - rig_[2];
      const float az_d = std::atan2(dx, -dz);
      const float az_f = std::atan2(f_tracker[0], -f_tracker[2]);
      yaw_ = az_d - az_f;
      yaw_pending_ = false;
      LOGI("world aligned with the viewer's heading: yaw=%.2f", yaw_);
    }

    head_view = Mul(Mul(pose_rot, RotY(yaw_)), Translate(-rig_[0], -rig_[1], -rig_[2]));
    std::memcpy(eye_pos, rig_, sizeof(eye_pos));

    // Gaze guidance: instead of turning the world, point an arrow at the focused object when it is off to the side.
    arrow_alpha = 0;
    if (focus_node_ >= 0 && focus_node_ < static_cast<int>(nodes_.size()) && nodes_[focus_node_].alpha > 0.3f) {
      const Node& fn = nodes_[focus_node_];
      const M4 pv = Mul(pose_rot, RotY(yaw_));
      const float wx = fn.world[0] - rig_[0], wy = fn.world[1] - rig_[1], wz = fn.world[2] - rig_[2];
      const float hx = pv[0] * wx + pv[4] * wy + pv[8] * wz;
      const float hy = pv[1] * wx + pv[5] * wy + pv[9] * wz;
      const float hz = pv[2] * wx + pv[6] * wy + pv[10] * wz;
      const float fwd = -hz;
      const float off = std::atan2(std::sqrt(hx * hx + hy * hy), fwd);  // angle from the line of sight
      const float deg = off * 180.f / kPi;
      if (deg > 30.f) {
        arrow_theta = std::atan2(hy, hx);
        arrow_alpha = std::min(1.f, (deg - 30.f) / 15.f) * (0.55f + 0.35f * std::sin(t * 6.f));
      }
    }

    // World-space horizontal gaze, and the look-down menu gesture (pitch below about -60 degrees).
    {
      const float c0 = std::cos(-yaw_), s0 = std::sin(-yaw_);
      const float gx = c0 * f_tracker[0] + s0 * f_tracker[2], gz = -s0 * f_tracker[0] + c0 * f_tracker[2];
      const float hl = std::sqrt(gx * gx + gz * gz);
      if (f_tracker[1] > -0.5f && hl > 0.3f) {  // looking roughly ahead: remember the heading
        last_heading_[0] = gx / hl;
        last_heading_[1] = gz / hl;
      }
      {  // which labelled node is the viewer looking at (used to reveal its label)
        float gw[3] = {gx, f_tracker[1], gz};
        int best = -1;
        float best_a = 1e9f;
        for (size_t i = 0; i < nodes_.size(); ++i) {
          const Node& nd = nodes_[i];
          if (!nd.has_label || nd.alpha < 0.3f) continue;
          float dv[3] = {nd.world[0] - rig_[0], nd.world[1] - rig_[1], nd.world[2] - rig_[2]};
          const float dist = std::sqrt(dv[0] * dv[0] + dv[1] * dv[1] + dv[2] * dv[2]);
          if (dist < 1e-3f) continue;
          const float cs = (dv[0] * gw[0] + dv[1] * gw[1] + dv[2] * gw[2]) / dist;
          const float ang = std::acos(std::min(1.f, std::max(-1.f, cs)));
          const float rr = nd.scale * std::max(nd.stretch[0], std::max(nd.stretch[1], nd.stretch[2]));
          const float lim = std::max(0.17f, std::asin(std::min(1.f, rr / dist)));
          if (ang < lim && ang < best_a) { best = static_cast<int>(i); best_a = ang; }
        }
        looked_ = best;
      }
      if (f_tracker[1] < -0.87f) {
        if (down_since_ == 0) down_since_ = now;
        if (down_armed_ && now - down_since_ > 1.5) {
          down_armed_ = false;
          open_menu = true;
          std::memcpy(menu_rig, rig_, sizeof(menu_rig));
          std::memcpy(menu_heading, last_heading_, sizeof(menu_heading));
        }
      } else {
        down_since_ = 0;
        if (f_tracker[1] > -0.5f) down_armed_ = true;
      }
    }

    // Gaze selection.
    if (gaze_enabled_) {
      // World-space gaze = R_y(-yaw) * f_tracker.
      const float c = std::cos(-yaw_), s = std::sin(-yaw_);
      const float g[3] = {c * f_tracker[0] + s * f_tracker[2], f_tracker[1],
                          -s * f_tracker[0] + c * f_tracker[2]};
      const int hit = PickNode(g, eye_pos);
      if (hit != gaze_target_) {
        gaze_target_ = hit;
        gaze_since_ = now;
      }
      gaze_progress_ = hit >= 0 ? static_cast<float>((now - gaze_since_) / kGazeDwellSeconds) : 0;
      if (hit >= 0 && (gaze_progress_ >= 1.f || trigger_pending_)) {
        gaze_fire = nodes_[hit].pick_id;
        gaze_target_ = -1;
        gaze_progress_ = 0;
        gaze_enabled_ = false;
      }
    }
    // A tap with no selection active recentres the view on the current focus.
    trigger_pending_ = false;

    // Subtitle texture.
    if (subtitle_ != applied_subtitle_) {
      applied_subtitle_ = subtitle_;
      subtitle_tex_ = 0;
      if (!subtitle_.empty())
        subtitle_tex_ = GetTexture(env, "sub:" + subtitle_, &subtitle_aspect_);
    }
    subtitle_tex = subtitle_tex_;
    subtitle_aspect = subtitle_aspect_;
    fade_now = fade_;
  }

  if (open_menu) env->CallVoidMethod(activity_, look_down_mid_, menu_rig[0], menu_rig[1], menu_rig[2], menu_heading[0], menu_heading[1]);
  if (gaze_fire >= 0) env->CallVoidMethod(activity_, on_gaze_mid_, gaze_fire);

  glBindFramebuffer(GL_FRAMEBUFFER, framebuffer_);
  glDisable(GL_SCISSOR_TEST);
  glEnable(GL_DEPTH_TEST);
  glEnable(GL_BLEND);
  glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
  glClearColor(0.01f, 0.01f, 0.03f, 1);
  glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
  glUseProgram(program_);

  std::lock_guard<std::mutex> lock(mu_);
  // The first node flagged as a light illuminates lit nodes; without one,
  // lit nodes are lit from the viewer.
  float light[3] = {eye_pos[0], eye_pos[1], eye_pos[2]};
  for (const Node& n : nodes_)
    if (n.flags & kLight) {
      std::memcpy(light, n.world, sizeof(light));
      break;
    }
  glUniform3fv(u_light_, 1, light);
  float light_positions[kMaxLights * 4] = {}, light_colors[kMaxLights * 4] = {};
  float light_directions[kMaxLights * 4] = {}, light_cones[kMaxLights * 2] = {};
  int dynamic_count = 0;
  for (const Node& node : nodes_) {
    if (node.kind != kDynamicLight || dynamic_count >= kMaxLights) continue;
    const LightConfig& config = node.light;
    auto direction = config.direction;
    if (config.aim >= 0 && config.aim < static_cast<int>(nodes_.size())) {
      for (int axis = 0; axis < 3; axis++) direction[axis] = nodes_[config.aim].world[axis] - node.world[axis];
      direction = UnitDirection(direction);
    }
    for (int axis = 0; axis < 3; axis++) {
      light_positions[dynamic_count * 4 + axis] = node.world[axis];
      light_colors[dynamic_count * 4 + axis] = config.color[axis];
      light_directions[dynamic_count * 4 + axis] = direction[axis];
    }
    light_positions[dynamic_count * 4 + 3] = config.range;
    light_colors[dynamic_count * 4 + 3] = config.intensity * node.alpha * LightModulation(config, t);
    light_directions[dynamic_count * 4 + 3] = config.spot ? 1.f : 0.f;
    light_cones[dynamic_count * 2] = std::cos(config.angle);
    light_cones[dynamic_count * 2 + 1] = std::cos(config.angle * (1 - std::max(0.001f, config.softness)));
    dynamic_count++;
  }
  glUniform4fv(u_light_positions_, kMaxLights, light_positions);
  glUniform4fv(u_light_colors_, kMaxLights, light_colors);
  glUniform4fv(u_light_directions_, kMaxLights, light_directions);
  glUniform2fv(u_light_cones_, kMaxLights, light_cones);
  glUniform1f(u_dynamic_count_, static_cast<float>(dynamic_count));
  ApplyFog(program_, eye_pos, true);
  const float pulse_phase = 1.f + 0.07f * std::sin(t * 5.f);

  for (int eye = 0; eye < 2; ++eye) {
    glViewport(eye == kLeft ? 0 : screen_width_ / 2, 0, screen_width_ / 2,
               screen_height_);
    const M4 eye_view = Mul(FromGl(eye_from_head_[eye]), head_view);
    const M4 vp = Mul(FromGl(projection_[eye]), eye_view);

    // Pass 1: sky, opaque spheres. Pass 2: blended discs/quads.
    for (int pass = 0; pass < 2; ++pass) {
      for (size_t i = 0; i < nodes_.size(); ++i) {
        const Node& n = nodes_[i];
        const bool opaque = n.kind != kDisc && n.kind != kQuad && n.alpha > 0.99f && n.color[3] > 0.99f;
        if ((pass == 0) != opaque) continue;
        const float pulse = (static_cast<int>(i) == highlight_) ? pulse_phase : 1.f;
        glEnable(GL_DEPTH_TEST);
        DrawNode(n, vp.data(), eye_pos, nullptr, false, t,
                 static_cast<int>(i) == gaze_target_ ? 1.12f : pulse);
      }
    }

    DrawLightBeams(vp.data(), eye_pos, t);
    DrawParticles(vp.data(), eye_pos, screen_height_ * std::abs(projection_[eye][5]) * 0.5f);

    // HUD: subtitle and reticle, drawn in head space (no world rotation).
    glUniform1f(u_fog_enabled_, 0);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_CULL_FACE);
    const M4 hud_vp = Mul(FromGl(projection_[eye]), FromGl(eye_from_head_[eye]));
    if (subtitle_tex) {
      // Fixed width (about 35 degrees across) so long narration stays legible; the panel grows
      // upward from the bottom edge as it wraps onto more lines.
      const float half_w = 0.50f;
      const float h = std::min(0.30f, half_w / std::max(0.5f, subtitle_aspect));
      const M4 model = Mul(Translate(0, -0.50f, -1.4f),
                           Scale(h * subtitle_aspect, h, 1));
      const float white[4] = {1, 1, 1, 1};
      glUniformMatrix4fv(u_model_, 1, GL_FALSE, model.data());
      glUniformMatrix4fv(u_vp_, 1, GL_FALSE, hud_vp.data());
      glUniform4fv(u_color_, 1, white);
      glUniform1f(u_lit_, 0);
      glUniform1f(u_inner_, 0);
      glUniform1f(u_use_tex_, 1);
      glBindTexture(GL_TEXTURE_2D, subtitle_tex);
      DrawMesh(quad_, a_pos_, a_nrm_, a_uv_, a_col_);
    }
    {
      float aspect = 1;
      const GLuint rt = GetTexture(env, "builtin:reticle", &aspect);
      const float s = 0.018f * (1.f + 0.8f * std::min(1.f, gaze_progress_));
      const M4 model = Mul(Translate(0, 0, -1.2f), Scale(s, s, 1));
      const float col[4] = {1, 1, 1, gaze_enabled_ ? 0.95f : 0.45f};
      glUniformMatrix4fv(u_model_, 1, GL_FALSE, model.data());
      glUniformMatrix4fv(u_vp_, 1, GL_FALSE, hud_vp.data());
      glUniform4fv(u_color_, 1, col);
      glUniform1f(u_lit_, 0);
      glUniform1f(u_inner_, 0);
      glUniform1f(u_use_tex_, rt ? 1.f : 0.f);
      if (rt) glBindTexture(GL_TEXTURE_2D, rt);
      DrawMesh(quad_, a_pos_, a_nrm_, a_uv_, a_col_);
    }
    if (arrow_alpha > 0.02f) {
      float aspect = 1;
      const GLuint at = GetTexture(env, "builtin:arrow", &aspect);
      const float ring = 0.32f, s = 0.055f;
      const M4 model = Mul(Mul(Translate(ring * std::cos(arrow_theta), ring * std::sin(arrow_theta), -1.2f), RotZ(arrow_theta)), Scale(s, s, 1));
      const float col[4] = {1.f, 0.85f, 0.35f, arrow_alpha};
      glUniformMatrix4fv(u_model_, 1, GL_FALSE, model.data());
      glUniformMatrix4fv(u_vp_, 1, GL_FALSE, hud_vp.data());
      glUniform4fv(u_color_, 1, col);
      glUniform1f(u_lit_, 0);
      glUniform1f(u_inner_, 0);
      glUniform1f(u_use_tex_, at ? 1.f : 0.f);
      if (at) glBindTexture(GL_TEXTURE_2D, at);
      DrawMesh(quad_, a_pos_, a_nrm_, a_uv_, a_col_);
    }
    if (fade_now > 0.01f) {  // viewpoint change: black overlay covering the whole view
      const M4 model = Mul(Translate(0, 0, -0.4f), Scale(2.5f, 2.5f, 1));
      const float col[4] = {0, 0, 0, fade_now};
      glUniformMatrix4fv(u_model_, 1, GL_FALSE, model.data());
      glUniformMatrix4fv(u_vp_, 1, GL_FALSE, hud_vp.data());
      glUniform4fv(u_color_, 1, col);
      glUniform1f(u_lit_, 0);
      glUniform1f(u_inner_, 0);
      glUniform1f(u_use_tex_, 0);
      DrawMesh(quad_, a_pos_, a_nrm_, a_uv_, a_col_);
    }
  }

  CardboardDistortionRenderer_renderEyeToDisplay(
      distortion_renderer_, 0, 0, 0, screen_width_, screen_height_,
      &left_eye_tex_, &right_eye_tex_);
  CHECKGLERROR("OnDrawFrame");
}

}  // namespace genova
