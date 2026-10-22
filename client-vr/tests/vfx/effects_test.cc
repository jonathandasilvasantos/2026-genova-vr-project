#include "effects.h"

#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>

int main() {
  genova::EmitterConfig config;
  config.count = 9000;
  config.loop = false;
  config.life = 0.1f;
  config.speed = 2;
  config.spread = 0;
  config.gravity = -6;
  genova::ParticleEmitter emitter;
  emitter.Initialize(2, config);
  assert(emitter.particles.size() == 400);
  for (const auto& particle : emitter.particles) assert(!particle.active);
  emitter.Burst();
  for (const auto& particle : emitter.particles) assert(particle.active && particle.age == 0);
  emitter.Advance(0.05f, true);
  const auto position = emitter.Position(emitter.particles[0]);
  assert(std::abs(position[1] - (2 * 0.05f - 3 * 0.05f * 0.05f)) < 0.00001f);
  emitter.Advance(0.06f, true);
  for (const auto& particle : emitter.particles) assert(!particle.active);
  emitter.Advance(0.2f, true);
  for (const auto& particle : emitter.particles) assert(!particle.active);
  emitter.Burst();
  assert(emitter.particles[0].active);

  config.loop = true;
  config.drag = 0.5f;
  config.life = 3;
  for (int shape = 0; shape < 5; shape++) {
    config.shape = shape;
    config.extent = {1, 2, 3};
    config.direction = {0, 0, 0};
    config.spread = 3.141593f;
    emitter.Initialize(shape, config);
    for (int frame = 0; frame < 500; frame++) emitter.Advance(0.016f, true);
    for (const auto& particle : emitter.particles) {
      assert(particle.active && particle.age < config.life);
      const auto coordinates = emitter.Position(particle);
      for (float coordinate : coordinates) assert(std::isfinite(coordinate));
      for (int axis = 0; axis < 3; axis++) assert(std::abs(particle.origin[axis]) <= std::abs(config.extent[axis]));
    }
    for (int frame = 0; frame < 30; frame++) emitter.Advance(0.2f, false);
    for (const auto& particle : emitter.particles) assert(!particle.active);
    emitter.Advance(0.016f, true);
    for (const auto& particle : emitter.particles) assert(particle.active);
  }
  config.count = -1;
  config.life = std::numeric_limits<float>::quiet_NaN();
  emitter.Initialize(0, config);
  assert(emitter.particles.size() == 1 && emitter.config.life == 2);
  genova::LightConfig light;
  for (int mode = 0; mode < 5; mode++) {
    light.flicker = mode;
    for (int sample = 0; sample < 100; sample++) {
      const float intensity = genova::LightModulation(light, sample * 0.03f);
      assert(intensity >= 0 && intensity <= 1.0001f);
    }
  }
  light.flicker = 4;
  assert(genova::LightModulation(light, 0) == 0);
  light.burst_age = 0;
  assert(genova::LightModulation(light, 0) == 1);
  light.burst_age = 1;
  assert(genova::LightModulation(light, 0) == 0);
  assert(genova::FogAmount(20, 0, 0, 0, 0, 0) == 0);
  assert(genova::FogAmount(2, 0, 0.1f, 0, 0, 3) == 0);
  assert(genova::FogAmount(20, 0, 0.1f, 0, 0.5f, 0)
      > genova::FogAmount(20, 10, 0.1f, 0, 0.5f, 0));
  std::cout << "PASS: emitter budgets, shapes, bursts, signed gravity, drag, visibility, light modulation, fog\n";
}
