#ifndef GENOVA_VR_PLAYER_H_
#define GENOVA_VR_PLAYER_H_

#include <jni.h>

#include <map>
#include <mutex>
#include <string>
#include <vector>

#include <GLES2/gl2.h>
#include "cardboard.h"
#include "effects.h"
#include "model.h"
#include "util.h"

namespace genova {

// Node kinds understood by the player. The bundle format is generic: nothing
// here is specific to any subject matter.
enum NodeKind { kSphere = 0, kDisc = 1, kQuad = 2, kSky = 3, kBox = 4, kCylinder = 5, kCone = 6, kTorus = 7, kModel = 8, kEmitter = 9, kDynamicLight = 10, kEffectGroup = 11 };
enum NodeFlags { kBillboard = 1, kHud = 2, kLight = 4, kLabel = 8 };

// Number of floats per node in the array passed from Java (see
// schema/bundle.schema.json and ExperiencePlayer.java).
constexpr int kNodeStride = 64;

struct Node {
  int kind = kSphere;
  float pos[3] = {0, 0, 0};
  float scale = 1;
  float color[4] = {1, 1, 1, 1};
  bool lit = false;
  int parent = -1;
  float orbit_radius = 0, orbit_speed = 0, orbit_phase = 0;
  float spin = 0;
  float inner = 0;
  int pick_id = -1;
  int tex = -1;  // index into texture keys
  int flags = 0;
  float tilt = 0;
  float rot[3] = {0, 0, 0};      // extra Euler rotation (radians)
  float stretch[3] = {1, 1, 1};  // per-axis multiplier on scale
  bool has_label = false;        // some auto-label child hangs off this node
  float fade = 0;                // labels fade in only for the highlighted / looked-at parent
  float base_alpha = 1;          // visibility (tweened by step actions)
  float alpha = 1;               // effective opacity this frame (own x animation x parents)
  float anim_off[3] = {0, 0, 0}, anim_scale = 1, anim_rot[3] = {0, 0, 0}, anim_alpha = 1;
  float world[3] = {0, 0, 0};  // computed each frame
  EmitterConfig emitter;
  LightConfig light;
};

struct Mesh {
  GLuint vbo = 0, ibo = 0;
  int index_count = 0;
};

struct CachedTexture {
  GLuint id = 0;
  float aspect = 1;
};

class VrPlayer {
 public:
  VrPlayer(JavaVM* vm, jobject activity);
  ~VrPlayer();

  void OnSurfaceCreated(JNIEnv* env);
  void SetScreenParams(int width, int height);
  void OnDrawFrame(JNIEnv* env);
  void OnTriggerEvent();
  void Recenter();  // long press: bring the content in front of the viewer, hidden by a short fade
  void OnPause();
  void OnResume();
  void SwitchViewer();

  // Scene API, called from the Java main thread.
  void SetScene(const std::vector<float>& data,
                const std::vector<std::string>& tex_keys);
  void MoveCamera(float x, float y, float z, int focus_node, float seconds,
                  int follow_node);
  void SetHighlight(int node);
  void SetSubtitle(const std::string& text);
  void SetGazeEnabled(bool enabled);
  // Declarative animation (loops, paths) and step-driven tweens; see schema/bundle.schema.json.
  void SetAnimations(const std::vector<float>& data);
  void SetFog(const std::vector<float>& data);
  void Burst(int node);
  void TweenNode(int node, int prop, float x, float y, float z, float seconds);
  // Debug only: ignore the sensors and look straight ahead. Expires after 90 s, so a test instance left on
  // screen can never be mistaken for broken head tracking.
  void SetFixedPose(bool on, float seconds = 90.f);

 private:
  bool UpdateDeviceParams();
  void GlSetup();
  void GlTeardown();
  void BuildMeshes();
  void UpdateWorld(float t);
  void DrawNode(const Node& n, const float* vp, const float* eye_pos,
                const float* head_rot_inv_yaw, bool hud, float t, float pulse);
  GLuint GetTexture(JNIEnv* env, const std::string& key, float* aspect);
  const Model* GetModel(JNIEnv* env, const std::string& key);
  int PickNode(const float* gaze_world, const float* eye_pos);
  void UpdateEffects(float seconds, float delta);
  void DrawParticles(const float* vp, const float* eye, float projection_scale);
  void DrawLightBeams(const float* vp, const float* eye, float seconds);
  void ApplyFog(GLuint program, const float* eye, bool enabled);

  JavaVM* vm_;
  jobject activity_;
  jmethodID load_texture_mid_ = nullptr;
  jmethodID on_gaze_mid_ = nullptr;
  jmethodID model_path_mid_ = nullptr;
  jmethodID look_down_mid_ = nullptr;
  std::map<std::string, Model> model_cache_;

  CardboardHeadTracker* head_tracker_ = nullptr;
  CardboardLensDistortion* lens_distortion_ = nullptr;
  CardboardDistortionRenderer* distortion_renderer_ = nullptr;
  CardboardEyeTextureDescription left_eye_tex_, right_eye_tex_;

  bool screen_params_changed_ = false;
  bool device_params_changed_ = false;
  int screen_width_ = 0, screen_height_ = 0;
  float projection_[2][16];
  float eye_from_head_[2][16];
  GLuint depth_rb_ = 0, framebuffer_ = 0, texture_ = 0;

  GLuint program_ = 0;
  GLint a_pos_ = 0, a_nrm_ = 0, a_uv_ = 0, a_col_ = 0;
  GLint u_model_ = 0, u_vp_ = 0, u_color_ = 0, u_lit_ = 0, u_light_ = 0,
        u_inner_ = 0, u_tex_ = 0, u_use_tex_ = 0, u_sky_ = 0;
  GLint u_light_positions_ = 0, u_light_colors_ = 0, u_light_directions_ = 0, u_light_cones_ = 0;
  GLint u_dynamic_count_ = 0, u_fog_enabled_ = 0;
  GLuint particle_program_ = 0, particle_vbo_ = 0;
  GLint particle_position_ = 0, particle_color_ = 0, particle_size_ = 0, particle_style_ = 0;
  GLfloat point_size_max_ = 64;
  std::vector<ParticleEmitter> emitters_;
  std::vector<float> particle_vertices_;
  int alpha_particle_count_ = 0, additive_particle_count_ = 0;
  std::array<float, 7> fog_ = {0.7f, 0.75f, 0.8f, 0, 0, 0, 0};
  Mesh sphere_, disc_, quad_, box_, cylinder_, cone_, torus_;

  struct AnimRec {
    int type = 0, node = 0, prop = 0, loop = 0, ease = 0;
    float seconds = 1, delay = 0, from[3] = {0, 0, 0}, to[3] = {0, 0, 0};
    std::vector<float> pts;  // path points (x,y,z...)
  };
  struct Tween {
    int node = 0, prop = 0;
    float from[3] = {0, 0, 0}, to[3] = {0, 0, 0};
    double start = 0, dur = 0;
  };
  std::vector<AnimRec> anims_;
  std::vector<Tween> tweens_;

  std::mutex mu_;
  std::vector<Node> nodes_;
  std::vector<std::string> tex_keys_;
  std::map<std::string, CachedTexture> tex_cache_;
  std::string subtitle_;
  std::string applied_subtitle_;
  GLuint subtitle_tex_ = 0;
  float subtitle_aspect_ = 1;

  // Camera rig.
  float rig_[3] = {0, 1.6f, 0};
  float rig_from_[3] = {0, 1.6f, 0}, rig_to_[3] = {0, 1.6f, 0};
  double move_start_ = 0, move_dur_ = 0;
  int focus_node_ = -1;
  int follow_node_ = -1;  // camera rides this node, offset in rig_to_
  bool yaw_pending_ = false;
  // Comfort camera: viewpoint changes are a short fade-out/in (a "blink"), never a slide; the world is never
  // rotated under the viewer's head except once at the start of a scene (under the fade).
  int blink_phase_ = 0;  // 0 idle, 1 fading out, 2 fading in
  double blink_t0_ = 0;
  float blink_target_[3] = {0, 0, 0};
  bool blink_align_ = false;
  float fade_ = 0;
  void StartBlinkLocked(const float* target, bool align);
  float yaw_ = 0;
  int highlight_ = -1;
  int looked_ = -1;  // labelled node the viewer is looking at
  double last_frame_ = 0;

  // Gaze selection.
  bool fixed_pose_ = false;
  double fixed_until_ = 0;
  bool gaze_enabled_ = false;
  int gaze_target_ = -1;
  double gaze_since_ = 0;
  float gaze_progress_ = 0;
  bool trigger_pending_ = false;

  // Looking straight down for a moment opens the in-VR menu (handled in Java).
  double down_since_ = 0;
  bool down_armed_ = true;
  float last_heading_[2] = {0, -1};  // last horizontal gaze direction (x, z) in world space

  double t0_ = 0;
};

}  // namespace genova

#endif  // GENOVA_VR_PLAYER_H_
