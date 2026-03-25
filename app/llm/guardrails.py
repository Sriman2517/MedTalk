import re
from typing import Tuple

# A curated list of emergency phrases.
# We use regex word boundaries (\b) and handle optional apostrophes (can't/cant)
RED_FLAG_PATTERNS = [
    r"\b(chest pain|heavy chest|heart attack|heart rate)\b",
    r"\b(can'?t breathe|shortness of breath|choking|gasping)\b",
    r"\b(unconscious|fainted|passed out|blacked out|not waking up)\b",
    r"\b(heavy bleeding|gushing blood|won'?t stop bleeding|hemorrhage)\b",
    r"\b(seizure|convulsion|shaking violently|fits)\b",
    r"\b(stroke|face drooping|can'?t speak|paralyzed|numbness)\b",
    r"\b(poison|snake bite|drank acid|swallowed chemicals)\b"
]

# Pre-compile the regex patterns on server startup for maximum performance
COMPILED_FLAGS = [re.compile(pattern, re.IGNORECASE) for pattern in RED_FLAG_PATTERNS]


def check_for_red_flags(user_message: str) -> Tuple[bool, str]:
    """
    Scans the user's transcribed message for critical emergency keywords.
    O(N) time complexity, runs instantly before any external API calls.

    Returns:
        Tuple[bool, str]: (Is_Emergency, The specific word that triggered it)
    """
    for pattern in COMPILED_FLAGS:
        match = pattern.search(user_message)
        if match:
            # Return True and the exact phrase that triggered the alarm
            return True, match.group(0)

    return False, ""


# --- Quick Local Test ---
if __name__ == "__main__":
    print("Initializing MedTalk Guardrails...\n")

    test_messages = [
        "I have had a mild headache since yesterday.",
        "Help, my grandfather just fainted and is not waking up!",
        "I feel a crushing chest pain right now."
    ]

    for msg in test_messages:
        print(f"Patient: '{msg}'")
        is_emergency, trigger = check_for_red_flags(msg)

        if is_emergency:
            print(f"🚨 ALERT: Red Flag detected! (Trigger: '{trigger}')\n")
        else:
            print("✅ Status: Safe. Routing to Llama 3 for interview.\n")