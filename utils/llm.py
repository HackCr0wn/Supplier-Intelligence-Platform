
import os
from dotenv import load_dotenv
from openai import OpenAI
from loguru import logger

load_dotenv()

NVIDIA_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
GROQ_MODEL = "qwen/qwen3-32b"


def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.6, max_tokens: int = 4096) -> str:

    nvidia_key = os.getenv("NVIDIA_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")

    if nvidia_key:
        try:
            client = OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=nvidia_key
            )

            response = client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature,
                top_p=0.95,
                max_tokens=max_tokens,
                frequency_penalty=0,
                presence_penalty=0,
                stream=False
            )
            logger.debug(f"LLM response via NVIDIA NIM ({NVIDIA_MODEL})")
            return response.choices[0].message.content

        except Exception as e:
            logger.warning(f"NVIDIA NIM failed: {e}. Trying Groq fallback...")

    # Fallback to Groq
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)

            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=max_tokens,
            )
            logger.debug(f"LLM response via Groq ({GROQ_MODEL})")
            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Groq also failed: {e}")
            raise

    raise ValueError("No LLM API key found. Set NVIDIA_API_KEY or GROQ_API_KEY in .env")
