"""Embeds all archive entries that have no vector yet, so semantic search finds them.

Run from the repo root on the machine that holds data/: python -m scripts.backfill_embeddings
Needs GEMINI_KEYS (or GEMINI_API_KEY) in .env. Safe to run repeatedly.
"""
from dotenv import load_dotenv

load_dotenv()

from core import memory


if __name__ == "__main__":
    print(f"[Backfill] Done, {memory.backfill_embeddings()} entries embedded.")
