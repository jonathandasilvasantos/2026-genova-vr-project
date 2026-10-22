#include "effects.h"

#include <algorithm>
#include <cmath>

namespace genova {
namespace {
constexpr float kEffectsPi = 3.14159265358979f;
}

float Bounded(float value, float fallback, float minimum, float maximum) {
  return std::isfinite(value) ? std::clamp(value, minimum, maximum) : fallback;
}

std::array<float, 3> UnitDirection(std::array<float, 3> direction) {
  const float length = std::hypot(direction[0], direction[1], direction[2]);
  if (!std::isfinite(length) || length < 0.0001f) return {0, 1, 0};
  for (float& component : direction) component /= length;
  return direction;
}

float ParticleEmitter::Random() {
  random_state_ ^= random_state_ << 13;
  random_state_ ^= random_state_ >> 17;
  random_state_ ^= random_state_ << 5;
  return static_cast<float>(random_state_ & 0xffffffu) / 16777216.f;
}

void ParticleEmitter::Spawn(Particle* particle) {
  particle->origin = {0, 0, 0};
  const float azimuth = Random() * 2 * kEffectsPi;
  if (config.shape == 1) {
    const float height = Random() * 2 - 1;
    const float radius = std::cbrt(Random());
    const float horizontal = std::sqrt(std::max(0.f, 1 - height * height));
    particle->origin = {radius * horizontal * std::cos(azimuth), radius * height,
                        radius * horizontal * std::sin(azimuth)};
  } else if (config.shape == 2) {
    for (float& component : particle->origin) component = Random() * 2 - 1;
  } else if (config.shape == 3) {
    const float radius = std::sqrt(Random());
    particle->origin = {radius * std::cos(azimuth), 0, radius * std::sin(azimuth)};
  } else if (config.shape == 4) {
    const float fraction = Random() * 2 - 1;
    particle->origin = {fraction, fraction, fraction};
  }
  for (int axis = 0; axis < 3; axis++) particle->origin[axis] *= config.extent[axis];
  const auto direction = UnitDirection(config.direction);
  const auto tangent = UnitDirection(std::abs(direction[1]) < 0.99f
      ? std::array<float, 3>{-direction[2], 0, direction[0]}
      : std::array<float, 3>{1, 0, 0});
  const std::array<float, 3> bitangent = {
      direction[1] * tangent[2] - direction[2] * tangent[1],
      direction[2] * tangent[0] - direction[0] * tangent[2],
      direction[0] * tangent[1] - direction[1] * tangent[0]};
  const float cosine = 1 - Random() * (1 - std::cos(config.spread));
  const float sine = std::sqrt(std::max(0.f, 1 - cosine * cosine));
  const float rotation = Random() * 2 * kEffectsPi;
  for (int axis = 0; axis < 3; axis++) {
    particle->velocity[axis] = config.speed * (direction[axis] * cosine
        + sine * (tangent[axis] * std::cos(rotation) + bitangent[axis] * std::sin(rotation)));
  }
  particle->age = 0;
  particle->active = true;
}

void ParticleEmitter::Initialize(int node_index, EmitterConfig settings) {
  node = node_index;
  config = settings;
  config.count = std::clamp(config.count, 1, kMaxEmitterParticles);
  config.life = Bounded(config.life, 2, 0.1f, 30);
  config.speed = Bounded(config.speed, 1, 0, 30);
  config.gravity = Bounded(config.gravity, 0, -30, 30);
  config.drag = Bounded(config.drag, 0, 0, 10);
  config.spread = Bounded(config.spread, 0.785398f, 0, kEffectsPi);
  config.shape = std::clamp(config.shape, 0, 4);
  config.texture = std::clamp(config.texture, 0, 4);
  for (float& extent : config.extent) extent = Bounded(extent, 0, -1000, 1000);
  for (float& size : config.size) size = Bounded(size, 0.1f, 0, 10);
  for (float& color : config.start_color) color = Bounded(color, 1, 0, 1);
  for (float& color : config.end_color) color = Bounded(color, 0, 0, 1);
  random_state_ = 0x9e3779b9u ^ (static_cast<uint32_t>(node_index) + 1u);
  if (!random_state_) random_state_ = 1;
  particles.assign(config.count, Particle{});
  if (config.loop) {
    for (Particle& particle : particles) {
      Spawn(&particle);
      particle.age = Random() * config.life;
    }
  }
}

void ParticleEmitter::Burst() {
  for (Particle& particle : particles) Spawn(&particle);
}

void ParticleEmitter::Advance(float seconds, bool visible) {
  seconds = Bounded(seconds, 0, 0, 0.2f);
  for (Particle& particle : particles) {
    if (!particle.active) {
      if (config.loop && visible) Spawn(&particle);
      else continue;
    }
    particle.age += seconds;
    if (particle.age >= config.life) {
      const float remainder = std::fmod(particle.age, config.life);
      particle.active = false;
      if (config.loop && visible) {
        Spawn(&particle);
        particle.age = remainder;
      }
    }
  }
}

std::array<float, 3> ParticleEmitter::Position(const Particle& particle) const {
  std::array<float, 3> position = particle.origin;
  const float seconds = particle.age;
  if (config.drag > 0.0001f) {
    const float displacement = -std::expm1(-config.drag * seconds) / config.drag;
    for (int axis = 0; axis < 3; axis++) position[axis] += particle.velocity[axis] * displacement;
    position[1] += config.gravity / config.drag * (seconds - displacement);
  } else {
    for (int axis = 0; axis < 3; axis++) position[axis] += particle.velocity[axis] * seconds;
    position[1] += 0.5f * config.gravity * seconds * seconds;
  }
  return position;
}

float LightModulation(const LightConfig& light, float seconds) {
  switch (light.flicker) {
    case 1: return 0.8f + 0.12f * std::sin(seconds * 17) + 0.08f * std::sin(seconds * 29);
    case 2: return 0.6f + 0.4f * std::sin(seconds * 3);
    case 3: return std::fmod(std::max(0.f, seconds), 0.6f) < 0.12f ? 1.f : 0.f;
    case 4: return light.burst_age < 0.5f ? std::exp(-light.burst_age * 10) : 0.f;
    default: return 1.f;
  }
}

float FogAmount(float distance, float height, float density, float base, float falloff, float start) {
  const float attenuation = std::exp(std::clamp(-(height - base) * falloff, -10.f, 10.f));
  return 1 - std::exp(-std::max(0.f, density) * std::max(0.f, distance - start) * attenuation);
}

}
