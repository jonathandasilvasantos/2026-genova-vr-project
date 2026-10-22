#!/bin/sh
set -eu
android_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output="$android_dir/build/vfx-validation"
mkdir -p "$output/classes"
: "${JAVA_HOME:?Set JAVA_HOME to a JDK 17 or newer}"
: "${JSON_JAR:?Set JSON_JAR to an executable org.json implementation, not the SDK stub android.jar}"
"${CXX:-clang++}" -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  -I"$android_dir/app/src/main/jni" "$android_dir/app/src/main/jni/effects.cc" \
  "$android_dir/tests/vfx/effects_test.cc" -o "$output/effects-test"
"$output/effects-test"
"$JAVA_HOME/bin/javac" -d "$output/classes" -cp "$JSON_JAR" \
  "$android_dir/app/src/main/java/com/google/cardboard/EffectsCatalog.java" \
  "$android_dir/app/src/main/java/com/google/cardboard/VisualEffects.java" \
  "$android_dir/tests/vfx/VisualEffectsTest.java"
"$JAVA_HOME/bin/java" -cp "$output/classes:$JSON_JAR" com.google.cardboard.VisualEffectsTest
