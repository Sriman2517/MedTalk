import os
import json
from typing import List, Dict
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

# Initialize the Groq client
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# We explicitly tell the model to output ONLY valid JSON.
SUMMARIZER_PROMPT = """
You are an expert medical scribe AI. Your task is to review a transcript of an interview 
between a medical AI and a patient, and extract the key medical information into a 
structured clinical brief for a General Practitioner.

You MUST output your response strictly in JSON format matching the following structure:
{
    "chief_complaint": "Brief summary of the primary issue",
    "duration": "How long the symptom has been present",
    "severity": "Mild, Moderate, Severe, or Unknown",
    "associated_symptoms": ["List", "of", "other", "symptoms"],
    "patient_narrative": "A concise 2-3 sentence summary of the patient's exact experience",
    "red_flags_detected": true/false
}

Do not include any introductory text, markdown formatting, or explanations outside of the JSON object.
"""


def generate_medical_brief(chat_history: List[Dict[str, str]]) -> dict:
    """
    Takes the completed chat history and uses Llama 3 to generate a structured JSON medical brief.
    """
    # Convert the chat history into a single transcript string for the model to read
    transcript = ""
    for msg in chat_history:
        role = "Doctor" if msg["role"] == "assistant" else "Patient"
        transcript += f"{role}: {msg['content']}\n"

    messages = [
        {"role": "system", "content": SUMMARIZER_PROMPT},
        {"role": "user", "content": f"Here is the interview transcript to summarize:\n\n{transcript}"}
    ]

    try:
        # We use Llama 3 8B. Groq supports 'json_object' response format which forces valid JSON.
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            temperature=0.0,  # 0.0 means zero creativity, maximum precision
            response_format={"type": "json_object"}
        )

        raw_json_string = response.choices[0].message.content.strip()

        # Parse the string into a Python dictionary
        medical_brief = json.loads(raw_json_string)
        return medical_brief

    except json.JSONDecodeError:
        print("Error: The model did not return valid JSON.")
        return {"error": "Failed to parse medical brief."}
    except Exception as e:
        print(f"Error calling Groq API: {e}")
        return {"error": str(e)}


# --- Quick Local Test ---
if __name__ == "__main__":
    # Mock chat history that would normally come from your interviewer.py
    mock_history = [
        {"role": "assistant",
         "content": "I'm so sorry to hear that you're in pain. Can you tell me how long you've been having this pain in your bottom and stomach?"},
        {"role": "user", "content": "I have pain in my ass and stomach pain"},
        {"role": "assistant",
         "content": "So the pain has been going on for 3 days. Is the pain in your bottom and stomach constant, or does it come and go?"},
        {"role": "user", "content": "3 days"},
        {"role": "assistant",
         "content": "The pain gets worse at night. Have you had any fever, vomiting, or blood in your stool during these 3 days?"},
        {"role": "user", "content": "it come and go especially at night"},
        {"role": "assistant",
         "content": "So, no fever, vomiting, or blood in your stool. Does the pain in your bottom and stomach hurt more when you move around, like when you walk or cough?"},
        {"role": "user", "content": "no"},
        {"role": "assistant",
         "content": "The pain happens even when you're not moving. Have you noticed any changes in your bowel movements, like diarrhea or constipation, during these 3 days?"},
        {"role": "user", "content": "pain comes even when im stationary"},
        {"role": "assistant",
         "content": "So, your bowel movements have been normal. How bad is the pain, is it mild, moderate, or very severe?"},
        {"role": "user", "content": "no"},
        {"role": "assistant",
         "content": "The pain is moderate. Have you taken any medicines or done anything that helps make the pain a little better?"},
        {"role": "user", "content": "moderate"},
        {"role": "user", "content": "no i have not taken any pills"}
    ]

    print("Extracting Medical Brief...\n")
    brief = generate_medical_brief(mock_history)

    # Print the resulting dictionary nicely formatted
    print(json.dumps(brief, indent=4))