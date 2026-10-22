// Single translation unit that compiles the header-only glTF/stb implementations.
#define TINYGLTF_IMPLEMENTATION
#define STB_IMAGE_IMPLEMENTATION
#define TINYGLTF_NO_STB_IMAGE_WRITE
#define TINYGLTF_NO_EXTERNAL_IMAGE
#define TINYGLTF_NO_INCLUDE_STB_IMAGE_WRITE
#include "tiny_gltf.h"

// The player only reads glTF; image writing is never used, so provide a stub.
namespace tinygltf {
bool WriteImageData(const std::string*, const std::string*, const Image*, bool, const FsCallbacks*,
                    const URICallbacks*, std::string*, void*) {
  return false;
}
}  // namespace tinygltf
