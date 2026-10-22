#ifndef GENOVA_EFFECTS_H_
#define GENOVA_EFFECTS_H_

#include <array>
#include <cstdint>
#include <vector>

namespace genova {

constexpr int kMaxEmitters = 6;
constexpr int kMaxLights = 4;
constexpr int kMaxParticles = 1500;
constexpr int kMaxEmitterParticles = 400;

struct EmitterConfig {
  int count = 60;
  float life = 2, speed = 1, gravity = 0, drag = 0, spread = 0.785398f;
  std::array<float, 3> direction = {0, 1, 0};
  int shape = 0;
  std::array<float, 3> extent = {0, 0, 0};
  std::array<float, 2> size = {0.1f, 0.1f};
  std::array<float, 4> start_color = {1, 1, 1, 1}, end_color = {1, 1, 1, 0};
  bool additive = true, loop = true;
  int texture = 0;
};

struct LightConfig {
  bool spot = false, beam = false;
  std::array<float, 3> color = {1, 1, 1}, direction = {0, -1, 0};
  float intensity = 1, range = 8, angle = 0.610865f, softness = 0.3f;
  int flicker = 0, aim = -1;
  float burst_age = 100;
};

struct Particle {
  std::array<float, 3> origin = {0, 0, 0}, velocity = {0, 0, 0};
  float age = 0;
  bool active = false;
};

class ParticleEmitter {
 public:
  int node = -1;
  EmitterConfig config;
  std::vector<Particle> particles;
  void Initialize(int node_index, EmitterConfig settings);
  void Burst();
  void Advance(float seconds, bool visible);
  std::array<float, 3> Position(const Particle& particle) const;

 private:
  uint32_t random_state_ = 1;
  float Random();
  void Spawn(Particle* particle);
};

float Bounded(float value, float fallback, float minimum, float maximum);
std::array<float, 3> UnitDirection(std::array<float, 3> direction);
float LightModulation(const LightConfig& light, float seconds);
float FogAmount(float distance, float height, float density, float base, float falloff, float start);

}

#endif
