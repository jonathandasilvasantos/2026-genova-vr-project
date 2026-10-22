#include "model.h"

#include <android/log.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>

#include "tiny_gltf.h"

#define MTAG "GenovaModel"
#define MLOG(...) __android_log_print(ANDROID_LOG_INFO, MTAG, __VA_ARGS__)
#define MERR(...) __android_log_print(ANDROID_LOG_ERROR, MTAG, __VA_ARGS__)

namespace genova {
namespace {

Mat4 Identity() {
  Mat4 m{};
  m[0] = m[5] = m[10] = m[15] = 1;
  return m;
}

Mat4 Mul(const Mat4& a, const Mat4& b) {
  Mat4 r{};
  for (int c = 0; c < 4; ++c)
    for (int row = 0; row < 4; ++row) {
      float s = 0;
      for (int k = 0; k < 4; ++k) s += a[k * 4 + row] * b[c * 4 + k];
      r[c * 4 + row] = s;
    }
  return r;
}

Mat4 FromTRS(const double* t, const double* q, const double* s) {
  const double x = q[0], y = q[1], z = q[2], w = q[3];
  Mat4 m = Identity();
  m[0] = static_cast<float>((1 - 2 * (y * y + z * z)) * s[0]);
  m[1] = static_cast<float>((2 * (x * y + z * w)) * s[0]);
  m[2] = static_cast<float>((2 * (x * z - y * w)) * s[0]);
  m[4] = static_cast<float>((2 * (x * y - z * w)) * s[1]);
  m[5] = static_cast<float>((1 - 2 * (x * x + z * z)) * s[1]);
  m[6] = static_cast<float>((2 * (y * z + x * w)) * s[1]);
  m[8] = static_cast<float>((2 * (x * z + y * w)) * s[2]);
  m[9] = static_cast<float>((2 * (y * z - x * w)) * s[2]);
  m[10] = static_cast<float>((1 - 2 * (x * x + y * y)) * s[2]);
  m[12] = static_cast<float>(t[0]);
  m[13] = static_cast<float>(t[1]);
  m[14] = static_cast<float>(t[2]);
  return m;
}

// Reads element `i` of an accessor as up to `comps` floats (normalised ints are mapped to 0..1).
bool ReadAttr(const tinygltf::Model& g, const tinygltf::Accessor& a, size_t i, float* out, int comps) {
  if (a.bufferView < 0) return false;
  const tinygltf::BufferView& bv = g.bufferViews[a.bufferView];
  const tinygltf::Buffer& buf = g.buffers[bv.buffer];
  const int n = tinygltf::GetNumComponentsInType(a.type);
  const int csz = tinygltf::GetComponentSizeInBytes(a.componentType);
  const size_t stride = a.ByteStride(bv) ? a.ByteStride(bv) : static_cast<size_t>(n * csz);
  const size_t off = bv.byteOffset + a.byteOffset + i * stride;
  if (off + n * csz > buf.data.size()) return false;
  const unsigned char* p = &buf.data[off];
  for (int c = 0; c < comps; ++c) {
    float v = c == 3 ? 1.f : 0.f;
    if (c < n) {
      switch (a.componentType) {
        case TINYGLTF_COMPONENT_TYPE_FLOAT: std::memcpy(&v, p + c * 4, 4); break;
        case TINYGLTF_COMPONENT_TYPE_UNSIGNED_BYTE: v = p[c] / 255.f; break;
        case TINYGLTF_COMPONENT_TYPE_BYTE: v = std::max(static_cast<signed char>(p[c]) / 127.f, -1.f); break;
        case TINYGLTF_COMPONENT_TYPE_UNSIGNED_SHORT: {
          unsigned short s;
          std::memcpy(&s, p + c * 2, 2);
          v = s / 65535.f;
          break;
        }
        case TINYGLTF_COMPONENT_TYPE_SHORT: {
          short s;
          std::memcpy(&s, p + c * 2, 2);
          v = std::max(s / 32767.f, -1.f);
          break;
        }
        default: v = 0;
      }
    }
    out[c] = v;
  }
  return true;
}

uint32_t ReadIndex(const tinygltf::Model& g, const tinygltf::Accessor& a, size_t i) {
  const tinygltf::BufferView& bv = g.bufferViews[a.bufferView];
  const tinygltf::Buffer& buf = g.buffers[bv.buffer];
  const int csz = tinygltf::GetComponentSizeInBytes(a.componentType);
  const unsigned char* p = &buf.data[bv.byteOffset + a.byteOffset + i * csz];
  if (csz == 4) {
    uint32_t v;
    std::memcpy(&v, p, 4);
    return v;
  }
  if (csz == 2) {
    uint16_t v;
    std::memcpy(&v, p, 2);
    return v;
  }
  return *p;
}

GLuint UploadTexture(const tinygltf::Image& img) {
  if (img.image.empty() || img.width <= 0 || img.height <= 0 || img.component != 4 || img.bits != 8) return 0;
  GLuint id = 0;
  glGenTextures(1, &id);
  glBindTexture(GL_TEXTURE_2D, id);
  const bool pow2 = (img.width & (img.width - 1)) == 0 && (img.height & (img.height - 1)) == 0;
  glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, img.width, img.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, img.image.data());
  if (pow2) {
    glGenerateMipmap(GL_TEXTURE_2D);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);
  } else {
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
  }
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
  return id;
}

// ---- animation sampling ----------------------------------------------------------------------
int FindKey(const std::vector<float>& times, float t) {
  // index i with times[i] <= t < times[i+1]
  int lo = 0, hi = static_cast<int>(times.size()) - 1;
  while (lo < hi) {
    const int mid = (lo + hi + 1) / 2;
    if (times[mid] <= t) lo = mid; else hi = mid - 1;
  }
  return lo;
}

void Slerp(const double* a, const double* b, double u, double* out) {
  double dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  double bb[4] = {b[0], b[1], b[2], b[3]};
  if (dot < 0) {
    dot = -dot;
    for (double& v : bb) v = -v;
  }
  double ka, kb;
  if (dot > 0.9995) {
    ka = 1 - u;
    kb = u;
  } else {
    const double th = std::acos(dot), sn = std::sin(th);
    ka = std::sin((1 - u) * th) / sn;
    kb = std::sin(u * th) / sn;
  }
  double len = 0;
  for (int i = 0; i < 4; ++i) {
    out[i] = ka * a[i] + kb * bb[i];
    len += out[i] * out[i];
  }
  len = std::sqrt(len);
  if (len > 0) for (int i = 0; i < 4; ++i) out[i] /= len;
}

void Sample(const ModelChannel& ch, float t, double* out) {
  const int comps = ch.path == 1 ? 4 : 3;
  const int stride = ch.interp == 2 ? comps * 3 : comps;  // cubic spline: in-tangent, value, out-tangent
  const int voff = ch.interp == 2 ? comps : 0;
  const int n = static_cast<int>(ch.times.size());
  auto val = [&](int k, int c) { return static_cast<double>(ch.values[k * stride + voff + c]); };
  if (n == 0) return;
  if (n == 1 || t <= ch.times.front()) {
    for (int c = 0; c < comps; ++c) out[c] = val(0, c);
    return;
  }
  if (t >= ch.times.back()) {
    for (int c = 0; c < comps; ++c) out[c] = val(n - 1, c);
    return;
  }
  const int i = FindKey(ch.times, t);
  const double span = ch.times[i + 1] - ch.times[i];
  const double u = span > 0 ? (t - ch.times[i]) / span : 0;
  if (ch.interp == 1) {  // step
    for (int c = 0; c < comps; ++c) out[c] = val(i, c);
    return;
  }
  if (ch.path == 1) {
    double a[4], b[4];
    for (int c = 0; c < 4; ++c) { a[c] = val(i, c); b[c] = val(i + 1, c); }
    Slerp(a, b, u, out);
  } else {
    for (int c = 0; c < comps; ++c) out[c] = val(i, c) * (1 - u) + val(i + 1, c) * u;
  }
}

void BuildNodes(const tinygltf::Model& g, Model* m) {
  m->nodes.resize(g.nodes.size());
  for (size_t i = 0; i < g.nodes.size(); ++i) {
    const tinygltf::Node& n = g.nodes[i];
    ModelNode& d = m->nodes[i];
    d.children = n.children;
    if (n.matrix.size() == 16) {
      d.has_matrix = true;
      for (int k = 0; k < 16; ++k) d.m[k] = n.matrix[k];
    }
    if (n.translation.size() == 3) for (int k = 0; k < 3; ++k) d.t[k] = n.translation[k];
    if (n.rotation.size() == 4) for (int k = 0; k < 4; ++k) d.r[k] = n.rotation[k];
    if (n.scale.size() == 3) for (int k = 0; k < 3; ++k) d.s[k] = n.scale[k];
  }
  for (size_t i = 0; i < m->nodes.size(); ++i)
    for (int c : m->nodes[i].children)
      if (c >= 0 && c < static_cast<int>(m->nodes.size())) m->nodes[c].parent = static_cast<int>(i);
  const int sc = g.defaultScene >= 0 ? g.defaultScene : 0;
  if (sc < static_cast<int>(g.scenes.size())) m->roots = g.scenes[sc].nodes;
}

void BuildChannels(const tinygltf::Model& g, Model* m) {
  for (const tinygltf::Animation& a : g.animations) {
    for (const tinygltf::AnimationChannel& c : a.channels) {
      int path;
      if (c.target_path == "translation") path = 0;
      else if (c.target_path == "rotation") path = 1;
      else if (c.target_path == "scale") path = 2;
      else continue;  // morph-target weights are not supported
      if (c.target_node < 0 || c.sampler < 0 || c.sampler >= static_cast<int>(a.samplers.size())) continue;
      const tinygltf::AnimationSampler& s = a.samplers[c.sampler];
      const tinygltf::Accessor& in = g.accessors[s.input];
      const tinygltf::Accessor& out = g.accessors[s.output];
      ModelChannel ch;
      ch.node = c.target_node;
      ch.path = path;
      ch.interp = s.interpolation == "STEP" ? 1 : s.interpolation == "CUBICSPLINE" ? 2 : 0;
      const int comps = path == 1 ? 4 : 3;
      const int per = ch.interp == 2 ? comps * 3 : comps;
      ch.times.resize(in.count);
      for (size_t i = 0; i < in.count; ++i) ReadAttr(g, in, i, &ch.times[i], 1);
      ch.values.resize(in.count * per);
      // Output accessor holds `per` scalars/vectors per key: read vec3/vec4 elements in order.
      const int elems = ch.interp == 2 ? 3 : 1;
      for (size_t i = 0; i < in.count; ++i)
        for (int e = 0; e < elems; ++e) {
          float v[4] = {0, 0, 0, 1};
          ReadAttr(g, out, i * elems + e, v, comps);
          for (int k = 0; k < comps; ++k) ch.values[i * per + e * comps + k] = v[k];
        }
      if (!ch.times.empty()) m->duration = std::max(m->duration, ch.times.back());
      m->channels.push_back(std::move(ch));
    }
  }
}

struct CpuPrim {
  std::vector<float> verts;  // 12 floats per vertex, node-local
  std::vector<unsigned short> idx;
  int texture_image = -1;
  float color[4] = {1, 1, 1, 1};
  int node = 0;
};

void CollectPrims(const tinygltf::Model& g, std::vector<CpuPrim>* out) {
  for (size_t ni = 0; ni < g.nodes.size(); ++ni) {
    const tinygltf::Node& node = g.nodes[ni];
    if (node.mesh < 0) continue;
    for (const tinygltf::Primitive& p : g.meshes[node.mesh].primitives) {
      if (p.mode != TINYGLTF_MODE_TRIANGLES && p.mode != -1) continue;
      auto pos_it = p.attributes.find("POSITION");
      if (pos_it == p.attributes.end()) continue;
      const tinygltf::Accessor& pa = g.accessors[pos_it->second];
      if (pa.count > 65535) {
        MERR("primitive with %zu vertices skipped (limit 65535)", pa.count);
        continue;
      }
      const tinygltf::Accessor* na = p.attributes.count("NORMAL") ? &g.accessors[p.attributes.at("NORMAL")] : nullptr;
      const tinygltf::Accessor* ua = p.attributes.count("TEXCOORD_0") ? &g.accessors[p.attributes.at("TEXCOORD_0")] : nullptr;
      const tinygltf::Accessor* ca = p.attributes.count("COLOR_0") ? &g.accessors[p.attributes.at("COLOR_0")] : nullptr;
      CpuPrim cp;
      cp.node = static_cast<int>(ni);
      for (size_t i = 0; i < pa.count; ++i) {
        float v[4] = {0, 0, 0, 1}, nrm[4] = {0, 1, 0, 0}, uv[4] = {0, 0, 0, 0}, col[4] = {1, 1, 1, 1};
        ReadAttr(g, pa, i, v, 3);
        if (na) ReadAttr(g, *na, i, nrm, 3);
        if (ua) ReadAttr(g, *ua, i, uv, 2);
        if (ca) ReadAttr(g, *ca, i, col, 4);
        cp.verts.insert(cp.verts.end(), {v[0], v[1], v[2], nrm[0], nrm[1], nrm[2], uv[0], uv[1], col[0], col[1], col[2], col[3]});
      }
      if (p.indices >= 0) {
        const tinygltf::Accessor& ia = g.accessors[p.indices];
        for (size_t i = 0; i < ia.count; ++i) cp.idx.push_back(static_cast<unsigned short>(ReadIndex(g, ia, i)));
      } else {
        for (size_t i = 0; i < pa.count; ++i) cp.idx.push_back(static_cast<unsigned short>(i));
      }
      if (p.material >= 0) {
        const tinygltf::Material& mat = g.materials[p.material];
        const auto& pbr = mat.pbrMetallicRoughness;
        if (pbr.baseColorFactor.size() == 4)
          for (int c = 0; c < 4; ++c) cp.color[c] = static_cast<float>(pbr.baseColorFactor[c]);
        if (pbr.baseColorTexture.index >= 0) cp.texture_image = g.textures[pbr.baseColorTexture.index].source;
        if (mat.emissiveFactor.size() == 3 && pbr.baseColorFactor.size() == 4) {
          const float e = static_cast<float>(std::max({mat.emissiveFactor[0], mat.emissiveFactor[1], mat.emissiveFactor[2]}));
          if (e > 0.5f)
            for (int c = 0; c < 3; ++c) cp.color[c] = std::min(1.f, cp.color[c] + static_cast<float>(mat.emissiveFactor[c]));
        }
      }
      out->push_back(std::move(cp));
    }
  }
}

}  // namespace

void EvalModel(const Model& m, float t, std::vector<Mat4>* out) {
  out->assign(m.nodes.size(), Identity());
  float tt = t;
  if (m.duration > 0) tt = std::fmod(t, m.duration);
  // local TRS, overridden by animation
  std::vector<std::array<double, 10>> trs(m.nodes.size());
  std::vector<bool> animated(m.nodes.size(), false);
  for (size_t i = 0; i < m.nodes.size(); ++i) {
    const ModelNode& n = m.nodes[i];
    trs[i] = {n.t[0], n.t[1], n.t[2], n.r[0], n.r[1], n.r[2], n.r[3], n.s[0], n.s[1], n.s[2]};
  }
  for (const ModelChannel& ch : m.channels) {
    if (ch.node < 0 || ch.node >= static_cast<int>(m.nodes.size())) continue;
    double v[4];
    Sample(ch, tt, v);
    animated[ch.node] = true;
    if (ch.path == 0) { for (int k = 0; k < 3; ++k) trs[ch.node][k] = v[k]; }
    else if (ch.path == 1) { for (int k = 0; k < 4; ++k) trs[ch.node][3 + k] = v[k]; }
    else { for (int k = 0; k < 3; ++k) trs[ch.node][7 + k] = v[k]; }
  }
  // depth-first from the roots so parents are ready before children
  std::vector<std::pair<int, Mat4>> stack;
  for (int r : m.roots) stack.push_back({r, Identity()});
  while (!stack.empty()) {
    auto [i, parent] = stack.back();
    stack.pop_back();
    if (i < 0 || i >= static_cast<int>(m.nodes.size())) continue;
    const ModelNode& n = m.nodes[i];
    Mat4 local;
    if (n.has_matrix && !animated[i]) {
      for (int k = 0; k < 16; ++k) local[k] = static_cast<float>(n.m[k]);
    } else {
      const auto& a = trs[i];
      local = FromTRS(&a[0], &a[3], &a[7]);
    }
    (*out)[i] = Mul(parent, local);
    for (int c : n.children) stack.push_back({c, (*out)[i]});
  }
}

bool LoadGlb(const std::string& path, Model* out) {
  tinygltf::TinyGLTF loader;
  tinygltf::Model g;
  std::string err, warn;
  if (!loader.LoadBinaryFromFile(&g, &err, &warn, path)) {
    MERR("cannot load %s: %s", path.c_str(), err.c_str());
    return false;
  }
  std::vector<CpuPrim> prims;
  CollectPrims(g, &prims);
  if (prims.empty()) {
    MERR("no triangles in %s", path.c_str());
    return false;
  }
  BuildNodes(g, out);
  BuildChannels(g, out);

  // Fit the bind pose (time 0) in a unit box: centre and scale are applied around the animated nodes.
  std::vector<Mat4> world;
  EvalModel(*out, 0.f, &world);
  float mn[3] = {1e30f, 1e30f, 1e30f}, mx[3] = {-1e30f, -1e30f, -1e30f};
  for (CpuPrim& p : prims) {
    float lo[3] = {1e30f, 1e30f, 1e30f}, hi[3] = {-1e30f, -1e30f, -1e30f};
    for (size_t i = 0; i + 11 < p.verts.size() + 0; i += 12)
      for (int a = 0; a < 3; ++a) {
        lo[a] = std::min(lo[a], p.verts[i + a]);
        hi[a] = std::max(hi[a], p.verts[i + a]);
      }
    const Mat4& w = world[p.node];
    for (int corner = 0; corner < 8; ++corner) {
      const float x = (corner & 1) ? hi[0] : lo[0], y = (corner & 2) ? hi[1] : lo[1], z = (corner & 4) ? hi[2] : lo[2];
      const float wx = w[0] * x + w[4] * y + w[8] * z + w[12];
      const float wy = w[1] * x + w[5] * y + w[9] * z + w[13];
      const float wz = w[2] * x + w[6] * y + w[10] * z + w[14];
      mn[0] = std::min(mn[0], wx); mx[0] = std::max(mx[0], wx);
      mn[1] = std::min(mn[1], wy); mx[1] = std::max(mx[1], wy);
      mn[2] = std::min(mn[2], wz); mx[2] = std::max(mx[2], wz);
    }
  }
  const float ext = std::max({mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]});
  out->scale = ext > 1e-9f ? 2.f / ext : 1.f;
  for (int a = 0; a < 3; ++a) out->center[a] = (mn[a] + mx[a]) / 2;

  std::vector<GLuint> tex_cache(g.images.size(), 0);
  for (CpuPrim& p : prims) {
    ModelPrim mp;
    glGenBuffers(1, &mp.vbo);
    glBindBuffer(GL_ARRAY_BUFFER, mp.vbo);
    glBufferData(GL_ARRAY_BUFFER, p.verts.size() * sizeof(float), p.verts.data(), GL_STATIC_DRAW);
    glGenBuffers(1, &mp.ibo);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, mp.ibo);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, p.idx.size() * sizeof(unsigned short), p.idx.data(), GL_STATIC_DRAW);
    mp.index_count = static_cast<int>(p.idx.size());
    std::memcpy(mp.color, p.color, sizeof(mp.color));
    mp.node = p.node;
    if (p.texture_image >= 0 && p.texture_image < static_cast<int>(g.images.size())) {
      if (!tex_cache[p.texture_image]) tex_cache[p.texture_image] = UploadTexture(g.images[p.texture_image]);
      mp.texture = tex_cache[p.texture_image];
    }
    out->prims.push_back(mp);
  }
  out->ok = true;
  MLOG("loaded %s: %zu primitives, %zu nodes, %zu animated channels, clip %.1fs", path.c_str(), out->prims.size(),
       out->nodes.size(), out->channels.size(), out->duration);
  return true;
}

void FreeModel(Model* m) {
  for (ModelPrim& p : m->prims) {
    glDeleteBuffers(1, &p.vbo);
    glDeleteBuffers(1, &p.ibo);
    if (p.texture) glDeleteTextures(1, &p.texture);
  }
  m->prims.clear();
  m->ok = false;
}

}  // namespace genova
