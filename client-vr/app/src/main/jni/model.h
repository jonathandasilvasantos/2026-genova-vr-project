#ifndef GENOVA_MODEL_H_
#define GENOVA_MODEL_H_

#include <array>
#include <string>
#include <vector>

#include <GLES2/gl2.h>

namespace genova {

using Mat4 = std::array<float, 16>;  // column-major

// One drawable piece of a .glb. Vertices stay in the glTF node's local space (not baked), so that
// animated nodes can move their meshes. Vertex layout: pos3 nrm3 uv2 col4.
struct ModelPrim {
  GLuint vbo = 0, ibo = 0;
  int index_count = 0;
  GLuint texture = 0;
  float color[4] = {1, 1, 1, 1};
  int node = 0;
  float lo[3] = {0, 0, 0}, hi[3] = {0, 0, 0};  // local bounds
};

struct ModelNode {
  int parent = -1;
  std::vector<int> children;
  double t[3] = {0, 0, 0}, r[4] = {0, 0, 0, 1}, s[3] = {1, 1, 1};
  bool has_matrix = false;
  double m[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
};

// A glTF animation channel: keyframes for one node property.
struct ModelChannel {
  int node = 0;
  int path = 0;    // 0 translation, 1 rotation, 2 scale
  int interp = 0;  // 0 linear, 1 step, 2 cubic spline (evaluated as linear on the values)
  std::vector<float> times;
  std::vector<float> values;  // 3 or 4 floats per key (cubic spline: 3 x that, we keep only the values)
};

struct Model {
  std::vector<ModelPrim> prims;
  std::vector<ModelNode> nodes;
  std::vector<int> roots;
  std::vector<ModelChannel> channels;
  float duration = 0;  // seconds; the clip loops (glTF leaves looping to the player)
  float center[3] = {0, 0, 0};
  float scale = 1;  // fits the bind-pose bounds in a box of half-extent 1
  bool ok = false;
};

// Parses and uploads a .glb. Must be called on the GL thread.
bool LoadGlb(const std::string& path, Model* out);
void FreeModel(Model* m);

// World matrix of every node at time `t` (seconds). `out` is resized to nodes.size().
void EvalModel(const Model& m, float t, std::vector<Mat4>* out);

}  // namespace genova

#endif  // GENOVA_MODEL_H_
