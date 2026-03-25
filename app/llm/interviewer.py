import os
from typing import List, Dict
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

# Initialize the Groq client. Ensure your API key is in your environment variables.
# export GROQ_API_KEY="your_api_key_here"
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

SYSTEM_PROMPT = """
You are 'MedTalk', a compassionate, highly focused junior doctor assisting a patient in rural India. 
Your goal is to gather symptom information to create a brief for a general practitioner.

CRITICAL RULES:
1. Ask EXACTLY ONE follow-up question at a time. Never ask multiple questions in a single response.
2. Keep your language extremely simple, as if speaking to someone with limited medical literacy.
3. Be empathetic but concise. 
4. If the patient describes a severe symptom (e.g., "chest pain", "can't breathe", "heavy bleeding", "unconscious"), immediately reply EXACTLY with this string: "[URGENT_RED_FLAG]: Needs immediate medical escalation."
5. Once you have enough information to understand the chief complaint, duration, and severity, reply exactly with: "[INTERVIEW_COMPLETE]"
"""

models = client.models.list()

for m in models.data:
    print(m.id)


def get_next_question(user_message: str, chat_history: List[Dict[str, str]]) -> str:
    """
    Takes the new user message and the existing chat history,
    calls Llama 3 via Groq, and returns the next appropriate doctor question.
    """
    # Append the latest user message to the history
    chat_history.append({"role": "user", "content": user_message})

    # Construct the messages payload
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + chat_history

    try:
        # Llama 3 8B or 70B are great for this. 8B is faster for real-time voice loops.
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.2,  # Low temperature for consistent, safe medical questioning
            max_tokens=150,  # Keep responses short
        )

        ai_response = response.choices[0].message.content.strip()

        # Save the AI's response to the history
        chat_history.append({"role": "assistant", "content": ai_response})

        return ai_response

    except Exception as e:
        print(f"Error calling Groq API: {e}")
        return "I am having trouble understanding right now. Can you please repeat your symptom?"


# --- Quick Local Test ---
if __name__ == "__main__":
    # Simulate a conversation state managed by your backend
    history = []

    print("System: MedTalk Initialized. Type 'quit' to exit.\n")
    while True:
        user_input = input("Patient: ")
        if user_input.lower() == 'quit':
            break

        reply = get_next_question(user_input, history)
        print(f"MedTalk: {reply}\n")

        # Break logic for our testing loop
        if "[INTERVIEW_COMPLETE]" in reply or "[URGENT_RED_FLAG]" in reply:
            print("--- Flow Ended ---")
            break