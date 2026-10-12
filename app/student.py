"""
app/student.py

Who answers the quiz questions?

  interactive : a human at the keyboard (the original tutorial behaviour)
  simulated   : a second LLM call playing a student with a persona
                (good / average / weak). This is how the drift lab and
                the dataset seeding run unattended.
  scripted    : answers supplied in advance (scenario files)

The point of the simulated student is honesty about signals: the lab has no
real users, so "user response" signals come from a simulator. The pattern is
the same; a real product plugs in real events.
"""

from __future__ import annotations

import os
from collections import deque

from opik import track

_mode = os.getenv("STUDENT_MODE", "interactive")
_persona = os.getenv("STUDENT_PERSONA", "average")
_scripted: deque[str] = deque()

PERSONAS = {
    "good": "You studied this topic carefully. Answer precisely in 1-2 sentences using the right terminology.",
    "average": "You half-remember this topic. Answer in one sentence; be roughly right but miss one detail.",
    "weak": "You barely remember this topic. Give a short, vague or partly wrong answer.",
}


def configure(mode: str | None = None, persona: str | None = None, answers: list[str] | None = None) -> None:
    global _mode, _persona
    if mode:
        _mode = mode
    if persona:
        _persona = persona
    if answers is not None:
        _scripted.clear()
        _scripted.extend(answers)


def mode() -> str:
    return _mode


@track(name="student.answer", type="general")
def answer(question: str, topic: str = "") -> str:
    """Return the student's answer to one quiz question."""
    if _mode == "interactive":
        return input("Your answer: ").strip()

    if _mode == "scripted" and _scripted:
        a = _scripted.popleft()
        print(f"Your answer (scripted): {a}")
        return a

    # simulated (also the fallback when a script runs out)
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_ollama import ChatOllama

    llm = ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0.7,
    )
    system = PERSONAS.get(_persona, PERSONAS["average"])
    resp = llm.invoke([
        SystemMessage(content=f"You are a student taking a quiz on '{topic}'. {system} Reply with the answer only."),
        HumanMessage(content=question),
    ])
    a = str(resp.content).strip()
    print(f"Your answer (simulated, {_persona}): {a[:120]}")
    return a
