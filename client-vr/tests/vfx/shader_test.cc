#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>

#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

std::string ShaderText(const std::string& source, const std::string& name) {
  const std::string marker = "constexpr const char* " + name + " = R\"glsl(";
  const size_t begin = source.find(marker);
  if (begin == std::string::npos) throw std::runtime_error("missing shader " + name);
  const size_t start = begin + marker.size();
  const size_t end = source.find(")glsl\";", start);
  std::string shader = source.substr(start, end - start);
  const std::string precision = "precision mediump float;";
  const size_t location = shader.find(precision);
  if (location != std::string::npos) shader.erase(location, precision.size());
  return "#version 120\n#define mediump\n" + shader;
}

GLuint Compile(GLenum kind, const std::string& source) {
  const GLuint shader = glCreateShader(kind);
  const char* contents = source.c_str();
  glShaderSource(shader, 1, &contents, nullptr);
  glCompileShader(shader);
  GLint success = 0;
  glGetShaderiv(shader, GL_COMPILE_STATUS, &success);
  if (!success) {
    char message[8192];
    glGetShaderInfoLog(shader, sizeof(message), nullptr, message);
    throw std::runtime_error(message);
  }
  return shader;
}

int main(int count, char** arguments) {
  if (count != 2) return 2;
  std::ifstream stream(arguments[1]);
  const std::string source((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
  CGLPixelFormatAttribute attributes[] = {kCGLPFAAccelerated, static_cast<CGLPixelFormatAttribute>(0)};
  CGLPixelFormatObj format = nullptr;
  GLint formats = 0;
  CGLContextObj context = nullptr;
  CGLError status = CGLChoosePixelFormat(attributes, &format, &formats);
  if (status != kCGLNoError || !format) { std::cerr << "OpenGL pixel format unavailable\n"; return 3; }
  status = CGLCreateContext(format, nullptr, &context);
  CGLDestroyPixelFormat(format);
  if (status != kCGLNoError || !context) { std::cerr << "OpenGL context unavailable\n"; return 3; }
  CGLSetCurrentContext(context);
  try {
    for (const auto& names : std::vector<std::pair<std::string, std::string>> {
        {"kVertexShader", "kFragmentShader"}, {"kParticleVertexShader", "kParticleFragmentShader"}}) {
      const GLuint vertex = Compile(GL_VERTEX_SHADER, ShaderText(source, names.first));
      const GLuint fragment = Compile(GL_FRAGMENT_SHADER, ShaderText(source, names.second));
      const GLuint program = glCreateProgram();
      glAttachShader(program, vertex);
      glAttachShader(program, fragment);
      glLinkProgram(program);
      GLint linked = 0;
      glGetProgramiv(program, GL_LINK_STATUS, &linked);
      if (!linked) {
        char message[8192];
        glGetProgramInfoLog(program, sizeof(message), nullptr, message);
        throw std::runtime_error(message);
      }
      glDeleteProgram(program);
      glDeleteShader(vertex);
      glDeleteShader(fragment);
    }
    std::cout << "PASS: translated GLES shaders compile/link on macOS OpenGL (not an Android rendering test)\n";
  } catch (const std::exception& failure) {
    std::cerr << failure.what() << '\n';
    CGLSetCurrentContext(nullptr);
    CGLDestroyContext(context);
    return 1;
  }
  CGLSetCurrentContext(nullptr);
  CGLDestroyContext(context);
}
