"""Canonical music moods. Every experience picks from these, so each mood is generated ONCE and reused
by every later experience (no new Lyria call). A 'custom' mood is allowed only when none fits."""

TAIL = " Instrumental only, steady even energy, no big ending, no fade-out."

MOODS = {
    "calm_wonder": ("Calm, wondrous exploration: airy pads, soft piano, light celesta, slow tempo, spacious and gentle.",
                    "gentle awe: space, nature, big ideas; good opener for young audiences"),
    "curious_discovery": ("Curious, lightly playful discovery: pizzicato strings, soft marimba, light flute, moderate tempo, bright and warm.",
                          "inquisitive, friendly learning: science, how-things-work, body, machines"),
    "gentle_focus": ("Quiet focused study: warm sustained strings, soft felt piano, minimal movement, slow and even.",
                     "concentration: detailed explanations, anatomy, maths, diagrams"),
    "warm_story": ("Warm storytelling: acoustic guitar, soft strings, gentle piano, relaxed tempo, heartfelt and human.",
                   "history, culture, biography, human stories"),
    "serene_nature": ("Serene nature ambience in music: soft flute, harp, light strings, slow flowing pulse, open and peaceful.",
                      "ecosystems, geography, weather, living things"),
    "deep_space_drift": ("Deep-space drift: low evolving synth pads, distant bells, slow shimmering textures, weightless and vast.",
                         "astronomy, physics, the universe, abstract big-scale ideas"),
    "epic_voyage": ("Hopeful epic voyage: gentle orchestral swells, soft brass, strings, steady medium tempo, sense of journey, never loud.",
                    "journeys, exploration, timelines, big transformations"),
    "reflective_calm": ("Reflective, tender calm: solo piano with soft strings, slow, spacious, quietly emotional.",
                        "summaries, reviews, closing scenes, ethics and reflection"),
    "light_mystery": ("Light, intriguing mystery: soft plucks, muted low strings, subtle pulse, curious not scary, slow-medium tempo.",
                      "puzzles, how-did-this-happen, forensics, hidden structures"),
    "bright_celebration": ("Bright, upbeat but gentle celebration: acoustic guitar, glockenspiel, light percussion, cheerful moderate tempo.",
                           "quizzes, achievements, finales for young audiences"),
    "playful_tinkering": ("Playful tinkering: quirky pizzicato, toy piano, soft woodblocks, light bouncy tempo, kind and funny.",
                          "engineering, experiments, hands-on and building topics for children"),
    "ocean_depth": ("Deep ocean calm: slow harp arpeggios, warm low pads, soft whale-like synth tones, floating and serene.",
                    "oceans, water cycle, underwater life"),
}


def prompt_for(mood: str) -> str:
    return MOODS[mood][0] + TAIL


def catalog() -> list[dict]:
    return [{"id": k, "description": v[1]} for k, v in MOODS.items()]
